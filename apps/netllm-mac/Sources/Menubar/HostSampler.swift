import Darwin
import Foundation
import IOKit

extension Notification.Name {
    static let hostSamplerDidUpdate = Notification.Name("HostSamplerDidUpdate")
}

struct HostSample: Sendable {
    var eCorePct: Double = 0
    var pCorePct: Double = 0
    var gpuPct: Double = 0
    var gpuMemoryGB: Double = 0
    var memoryUsedGB: Double = 0
    var memoryTotalGB: Double = 0
    var wiredGB: Double = 0
    var activeGB: Double = 0
    var compressedGB: Double = 0
    var freeGB: Double = 0
    var loadAvg: (Double, Double, Double) = (0, 0, 0)
    var thermal: String = "Nominal"
    var uptimeSeconds: TimeInterval = 0
}

@MainActor
final class HostSampler {
    static let shared = HostSampler()
    static let sparklineWindow = 8

    private var task: Task<Void, Never>?
    private(set) var current = HostSample()
    private(set) var eCoreHistory: [Double] = []
    private(set) var pCoreHistory: [Double] = []
    private(set) var gpuHistory: [Double] = []
    private(set) var gpuMemoryHistory: [Double] = []
    private var subscribers = 0
    private var lastCpuTicks: [[Int]] = []
    private var cpuCluster: (pCount: Int, eCount: Int)?

    func subscribe() {
        subscribers += 1
        if subscribers == 1 {
            lastCpuTicks = []
            primeSamples()
        }
        startIfNeeded()
    }

    func unsubscribe() {
        subscribers = max(0, subscribers - 1)
        if subscribers == 0 { stop() }
    }

    private func primeSamples() {
        Task {
            await sample()
            try? await Task.sleep(for: .milliseconds(250))
            await sample()
            notifyUpdate()
        }
    }

    private func startIfNeeded() {
        guard task == nil else { return }
        task = Task { [weak self] in
            while !Task.isCancelled {
                await self?.sample()
                self?.notifyUpdate()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    private func notifyUpdate() {
        NotificationCenter.default.post(name: .hostSamplerDidUpdate, object: self)
    }

    private func sample() async {
        var sample = HostSample()
        sample.thermal = thermalLabel()
        sample.uptimeSeconds = ProcessInfo.processInfo.systemUptime
        sample.loadAvg = loadAverage()
        sample.memoryTotalGB = Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824
        memoryStats(into: &sample)
        cpuStats(into: &sample)
        gpuStats(into: &sample)
        current = sample
        appendHistory(&eCoreHistory, sample.eCorePct)
        appendHistory(&pCoreHistory, sample.pCorePct)
        appendHistory(&gpuHistory, sample.gpuPct)
        appendHistory(&gpuMemoryHistory, sample.gpuMemoryGB)
    }

    private func appendHistory(_ buffer: inout [Double], _ value: Double) {
        buffer.append(value)
        if buffer.count > 60 { buffer.removeFirst(buffer.count - 60) }
    }

    private func thermalLabel() -> String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: return "Nominal"
        case .fair: return "Fair"
        case .serious: return "Serious"
        case .critical: return "Critical"
        @unknown default: return "Unknown"
        }
    }

    private func loadAverage() -> (Double, Double, Double) {
        var load = [Double](repeating: 0, count: 3)
        let count = load.withUnsafeMutableBufferPointer { ptr -> Int32 in
            guard let base = ptr.baseAddress else { return 0 }
            return getloadavg(base, 3)
        }
        guard count == 3 else { return (0, 0, 0) }
        return (load[0], load[1], load[2])
    }

    private func memoryStats(into sample: inout HostSample) {
        var stats = vm_statistics64()
        var count = mach_msg_type_number_t(
            MemoryLayout<vm_statistics64>.size / MemoryLayout<integer_t>.size
        )
        let result = withUnsafeMutablePointer(to: &stats) { ptr -> kern_return_t in
            ptr.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { intPtr in
                host_statistics64(mach_host_self(), HOST_VM_INFO64, intPtr, &count)
            }
        }
        guard result == KERN_SUCCESS else { return }
        let pageSize = Double(vm_kernel_page_size)
        let wired = Double(stats.wire_count) * pageSize
        let active = Double(stats.active_count) * pageSize
        let compressed = Double(stats.compressor_page_count) * pageSize
        let free = Double(stats.free_count + stats.inactive_count) * pageSize
        sample.wiredGB = wired / 1_073_741_824
        sample.activeGB = active / 1_073_741_824
        sample.compressedGB = compressed / 1_073_741_824
        sample.freeGB = free / 1_073_741_824
        sample.memoryUsedGB = sample.wiredGB + sample.activeGB + sample.compressedGB
        if sample.memoryTotalGB <= 0 {
            sample.memoryTotalGB = sample.memoryUsedGB + sample.freeGB
        }
    }

    private func cpuClusterCounts(numCpus: Int) -> (pCount: Int, eCount: Int) {
        if let cpuCluster { return cpuCluster }
        let pCount = sysctlInt("hw.perflevel1.logicalcpu") ?? numCpus / 2
        let eCount = sysctlInt("hw.perflevel0.logicalcpu") ?? max(0, numCpus - pCount)
        let resolved = (
            pCount: min(max(pCount, 0), numCpus),
            eCount: min(max(eCount, 0), max(0, numCpus - pCount))
        )
        cpuCluster = resolved
        return resolved
    }

    private func cpuStats(into sample: inout HostSample) {
        var cpuInfo: processor_info_array_t?
        var numCpuInfo: mach_msg_type_number_t = 0
        var numCpus: natural_t = 0
        let result = host_processor_info(
            mach_host_self(),
            PROCESSOR_CPU_LOAD_INFO,
            &numCpus,
            &cpuInfo,
            &numCpuInfo
        )
        guard result == KERN_SUCCESS, let cpuInfo else { return }
        defer { vm_deallocate(mach_task_self_, vm_address_t(bitPattern: cpuInfo), vm_size_t(numCpuInfo)) }

        let stateMax = Int(CPU_STATE_MAX)
        let cpuCount = Int(numCpus)
        var currentTicks: [[Int]] = []
        currentTicks.reserveCapacity(cpuCount)
        for cpu in 0..<cpuCount {
            let offset = cpu * stateMax
            var ticks: [Int] = []
            ticks.reserveCapacity(stateMax)
            for state in 0..<stateMax {
                ticks.append(Int(cpuInfo[offset + state]))
            }
            currentTicks.append(ticks)
        }

        defer { lastCpuTicks = currentTicks }

        guard lastCpuTicks.count == currentTicks.count, !lastCpuTicks.isEmpty else { return }

        let clusters = cpuClusterCounts(numCpus: cpuCount)
        var eUsage: [Double] = []
        var pUsage: [Double] = []
        eUsage.reserveCapacity(clusters.eCount)
        pUsage.reserveCapacity(clusters.pCount)

        for cpu in 0..<cpuCount {
            let usage = cpuUsagePercent(previous: lastCpuTicks[cpu], current: currentTicks[cpu])
            if cpu < clusters.pCount {
                pUsage.append(usage)
            } else if cpu < clusters.pCount + clusters.eCount {
                eUsage.append(usage)
            }
        }

        if !eUsage.isEmpty {
            sample.eCorePct = eUsage.reduce(0, +) / Double(eUsage.count)
        }
        if !pUsage.isEmpty {
            sample.pCorePct = pUsage.reduce(0, +) / Double(pUsage.count)
        } else if !eUsage.isEmpty {
            sample.pCorePct = sample.eCorePct
        }
    }

    private func cpuUsagePercent(previous: [Int], current: [Int]) -> Double {
        guard previous.count == current.count, !previous.isEmpty else { return 0 }
        let prevTotal = previous.reduce(0, +)
        let curTotal = current.reduce(0, +)
        let totalDelta = curTotal - prevTotal
        guard totalDelta > 0 else { return 0 }
        let prevIdle = previous[Int(CPU_STATE_IDLE)]
        let curIdle = current[Int(CPU_STATE_IDLE)]
        let idleDelta = curIdle - prevIdle
        let usedDelta = totalDelta - idleDelta
        return max(0, min(100, Double(usedDelta) / Double(totalDelta) * 100))
    }

    private func gpuStats(into sample: inout HostSample) {
        guard let match = IOServiceMatching("AGXAccelerator") else { return }
        var iterator: io_iterator_t = 0
        guard IOServiceGetMatchingServices(kIOMainPortDefault, match, &iterator) == KERN_SUCCESS else {
            return
        }
        defer { IOObjectRelease(iterator) }

        let entry = IOIteratorNext(iterator)
        guard entry != 0 else { return }
        defer { IOObjectRelease(entry) }

        var props: Unmanaged<CFMutableDictionary>?
        guard IORegistryEntryCreateCFProperties(
            entry,
            &props,
            kCFAllocatorDefault,
            0
        ) == KERN_SUCCESS,
            let dict = props?.takeRetainedValue() as? [String: Any],
            let perf = dict["PerformanceStatistics"] as? [String: Any]
        else { return }

        if let util = perf["Device Utilization %"] as? Int {
            sample.gpuPct = Double(util)
        } else if let util = perf["Device Utilization %"] as? Double {
            sample.gpuPct = util
        }

        let memBytes: UInt64
        if let inUse = perf["In use system memory"] as? Int {
            memBytes = UInt64(inUse)
        } else if let inUse = perf["In use system memory"] as? UInt64 {
            memBytes = inUse
        } else if let alloc = perf["Alloc system memory"] as? Int {
            memBytes = UInt64(alloc)
        } else if let alloc = perf["Alloc system memory"] as? UInt64 {
            memBytes = alloc
        } else {
            memBytes = 0
        }
        sample.gpuMemoryGB = Double(memBytes) / 1_073_741_824
    }

    private func sysctlInt(_ name: String) -> Int? {
        var value: Int32 = 0
        var size = MemoryLayout<Int32>.size
        let rc = sysctlbyname(name, &value, &size, nil, 0)
        guard rc == 0 else { return nil }
        return Int(value)
    }
}
