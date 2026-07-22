import Darwin
import Foundation

struct HostSample: Sendable {
    var eCorePct: Double = 0
    var pCorePct: Double = 0
    var gpuPct: Double = 0
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

    private var task: Task<Void, Never>?
    private(set) var current = HostSample()
    private(set) var eCoreHistory: [Double] = []
    private(set) var pCoreHistory: [Double] = []
    private(set) var gpuHistory: [Double] = []
    private var subscribers = 0

    func subscribe() {
        subscribers += 1
        startIfNeeded()
    }

    func unsubscribe() {
        subscribers = max(0, subscribers - 1)
        if subscribers == 0 { stop() }
    }

    private func startIfNeeded() {
        guard task == nil else { return }
        task = Task { [weak self] in
            while !Task.isCancelled {
                await self?.sample()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }

    private func sample() async {
        var sample = HostSample()
        sample.thermal = thermalLabel()
        sample.uptimeSeconds = ProcessInfo.processInfo.systemUptime
        sample.loadAvg = loadAverage()
        memoryStats(into: &sample)
        cpuStats(into: &sample)
        sample.gpuPct = min(sample.eCorePct + sample.pCorePct, 100) * 0.45
        current = sample
        appendHistory(&eCoreHistory, sample.eCorePct)
        appendHistory(&pCoreHistory, sample.pCorePct)
        appendHistory(&gpuHistory, sample.gpuPct)
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
        let total = wired + active + compressed + free
        sample.wiredGB = wired / 1_073_741_824
        sample.activeGB = active / 1_073_741_824
        sample.compressedGB = compressed / 1_073_741_824
        sample.freeGB = free / 1_073_741_824
        sample.memoryTotalGB = total / 1_073_741_824
        sample.memoryUsedGB = (wired + active + compressed) / 1_073_741_824
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

        var eTotal: Double = 0
        var pTotal: Double = 0
        var eCount = 0
        var pCount = 0
        let stateMax = Int(CPU_STATE_MAX)
        let stride = MemoryLayout<integer_t>.size * stateMax
        for cpu in 0..<Int(numCpus) {
            let offset = cpu * stride / MemoryLayout<integer_t>.size
            let user = Double(cpuInfo[offset + Int(CPU_STATE_USER)])
            let system = Double(cpuInfo[offset + Int(CPU_STATE_SYSTEM)])
            let idle = Double(cpuInfo[offset + Int(CPU_STATE_IDLE)])
            let total = user + system + idle
            guard total > 0 else { continue }
            let usage = (user + system) / total * 100
            if cpu < Int(numCpus) / 2 {
                eTotal += usage
                eCount += 1
            } else {
                pTotal += usage
                pCount += 1
            }
        }
        sample.eCorePct = eCount > 0 ? eTotal / Double(eCount) : 0
        sample.pCorePct = pCount > 0 ? pTotal / Double(pCount) : sample.eCorePct
    }
}
