import Darwin
import Foundation

enum AppControlClient {
    static func send(command: AppControlServer.Command) async -> AppControlServer.Response? {
        let socketURL = AppConfig.appSupportURL().appendingPathComponent("control.sock")
        let path = socketURL.path
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                continuation.resume(returning: performSend(command: command.rawValue, path: path))
            }
        }
    }

    private static func performSend(command: String, path: String) -> AppControlServer.Response? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        path.withCString { cstr in
            withUnsafeMutableBytes(of: &addr.sun_path) { raw in
                let dest = raw.baseAddress!.assumingMemoryBound(to: CChar.self)
                strncpy(dest, cstr, 104)
            }
        }
        let connectResult = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connectResult == 0 else { return nil }

        guard let body = try? JSONEncoder().encode(["command": command]) else { return nil }
        _ = body.withUnsafeBytes { write(fd, $0.baseAddress, body.count) }

        var buffer = [UInt8](repeating: 0, count: 8192)
        let n = read(fd, &buffer, buffer.count)
        guard n > 0 else { return nil }
        var responseData = Data(buffer.prefix(n))
        if responseData.last == 10 { responseData.removeLast() }
        return try? JSONDecoder().decode(AppControlServer.Response.self, from: responseData)
    }
}
