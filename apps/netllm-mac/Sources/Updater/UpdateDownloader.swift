import Foundation

/// URLSession download delegate that reports byte progress for large DMG assets.
final class UpdateDownloader: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private var continuation: CheckedContinuation<(URL, URLResponse), Error>?
    private let onProgress: (@Sendable (Double) -> Void)?

    init(onProgress: (@Sendable (Double) -> Void)? = nil) {
        self.onProgress = onProgress
    }

    private lazy var session: URLSession = {
        URLSession(configuration: .default, delegate: self, delegateQueue: nil)
    }()

    func cancel() {
        session.invalidateAndCancel()
        if let continuation {
            self.continuation = nil
            continuation.resume(throwing: CancellationError())
        }
    }

    func download(from url: URL) async throws -> (URL, URLResponse) {
        try await withCheckedThrowingContinuation { cont in
            continuation = cont
            session.downloadTask(with: url).resume()
        }
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        guard totalBytesExpectedToWrite > 0, let onProgress else { return }
        let fraction = Double(totalBytesWritten) / Double(totalBytesExpectedToWrite)
        onProgress(min(max(fraction, 0), 1))
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        guard let response = downloadTask.response else {
            continuation?.resume(throwing: UpdateError.downloadFailed("Missing response"))
            continuation = nil
            return
        }
        // URLSession deletes `location` when this delegate returns — stage before resuming.
        let staged = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: false)
            .appendingPathExtension("dmg")
        do {
            do {
                try FileManager.default.moveItem(at: location, to: staged)
            } catch {
                try FileManager.default.copyItem(at: location, to: staged)
            }
            continuation?.resume(returning: (staged, response))
        } catch {
            continuation?.resume(throwing: error)
        }
        continuation = nil
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let error, continuation != nil else { return }
        continuation?.resume(throwing: error)
        continuation = nil
    }
}
