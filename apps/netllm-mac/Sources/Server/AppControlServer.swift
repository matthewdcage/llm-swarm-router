import Foundation
import Darwin

@MainActor
protocol AppControlHandling: AnyObject {
    func handleAppControl(_ command: AppControlServer.Command) async -> AppControlServer.Response
}

final class AppControlServer: @unchecked Sendable {
    enum Command: String, Sendable {
        case start, stop, restart, status
    }

    struct Response: Encodable, Sendable {
        let ok: Bool
        let status: String
        let state: String
        let pid: Int32?
        let host: String
        let port: Int
        let message: String?

        static func success(
            status: String,
            state: ServerProcess.State,
            server: ServerProcess?,
            message: String? = nil
        ) -> Response {
            Response(
                ok: true,
                status: status,
                state: describe(state),
                pid: server?.pid,
                host: server?.host ?? "127.0.0.1",
                port: server?.port ?? 11400,
                message: message
            )
        }

        static func failure(
            status: String,
            state: ServerProcess.State,
            server: ServerProcess?,
            message: String
        ) -> Response {
            Response(
                ok: false,
                status: status,
                state: describe(state),
                pid: server?.pid,
                host: server?.host ?? "127.0.0.1",
                port: server?.port ?? 11400,
                message: message
            )
        }
    }

    private struct Request: Decodable { let command: String }

    weak var handler: AppControlHandling?
    private let socketURL: URL
    private let queue = DispatchQueue(label: "com.netllm.control")
    private var listenFD: Int32 = -1
    private var running = false

    init(socketURL: URL = AppConfig.appSupportURL().appendingPathComponent("control.sock")) {
        self.socketURL = socketURL
    }

    deinit { stop() }

    func start() throws {
        guard !running else { return }
        try FileManager.default.createDirectory(
            at: socketURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try? FileManager.default.removeItem(at: socketURL)

        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw POSIXError(.init(rawValue: errno) ?? .EIO) }

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let path = socketURL.path
        withUnsafeMutableBytes(of: &addr.sun_path) { raw in
            let c = raw.baseAddress!.assumingMemoryBound(to: CChar.self)
            _ = path.withCString { strncpy(c, $0, 104) }
        }
        let bindResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bindResult == 0 else {
            close(fd)
            throw POSIXError(.init(rawValue: errno) ?? .EIO)
        }
        listen(fd, 8)
        listenFD = fd
        running = true
        queue.async { [weak self] in self?.acceptLoop() }
    }

    func stop() {
        running = false
        if listenFD >= 0 { close(listenFD); listenFD = -1 }
        try? FileManager.default.removeItem(at: socketURL)
    }

    private func acceptLoop() {
        while running {
            let client = accept(listenFD, nil, nil)
            if client < 0 { usleep(50_000); continue }
            handle(clientFD: client)
        }
    }

    private func handle(clientFD: Int32) {
        defer { close(clientFD) }
        var buffer = [UInt8](repeating: 0, count: 4096)
        let n = read(clientFD, &buffer, buffer.count)
        guard n > 0 else { return }
        let data = Data(buffer.prefix(n))
        let response: Response
        do {
            let req = try JSONDecoder().decode(Request.self, from: data)
            guard let command = Command(rawValue: req.command) else {
                response = .failure(status: "error", state: .stopped, server: nil, message: "Unknown command")
                writeResponse(response, fd: clientFD)
                return
            }
            let sem = DispatchSemaphore(value: 0)
            let box = ResponseBox()
            Task { @MainActor [weak self] in
                if let handler = self?.handler {
                    box.value = await handler.handleAppControl(command)
                } else {
                    box.value = .failure(status: "error", state: .stopped, server: nil, message: "No handler")
                }
                sem.signal()
            }
            let waitSeconds: TimeInterval = (command == .start || command == .restart) ? 90 : 30
            _ = sem.wait(timeout: .now() + waitSeconds)
            response = box.value ?? .failure(status: "timeout", state: .stopped, server: nil, message: "Timed out")
        } catch {
            response = .failure(status: "error", state: .stopped, server: nil, message: "\(error)")
        }
        writeResponse(response, fd: clientFD)
    }

    private func writeResponse(_ response: Response, fd: Int32) {
        guard var data = try? JSONEncoder().encode(response) else { return }
        data.append(10)
        _ = data.withUnsafeBytes { write(fd, $0.baseAddress, data.count) }
    }

    static func describe(_ state: ServerProcess.State) -> String {
        switch state {
        case .stopped: return "stopped"
        case .starting: return "starting"
        case .running: return "running"
        case .stopping: return "stopping"
        case .unresponsive: return "unresponsive"
        case .failed: return "failed"
        }
    }
}

private final class ResponseBox: @unchecked Sendable {
    var value: AppControlServer.Response?
}
