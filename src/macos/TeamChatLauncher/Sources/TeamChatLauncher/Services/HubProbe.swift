import Foundation
import Network

private final class HubProbeCompletionState: @unchecked Sendable {
    private let lock = NSLock()
    private var resumed = false

    func claim() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard !resumed else { return false }
        resumed = true
        return true
    }
}

struct HubProbeResult: Equatable {
    let reachable: Bool
    let message: String
}

enum HubProbe {
    static func probe(address: String, timeout: TimeInterval = 2.0) async -> HubProbeResult {
        guard let parts = HubAddressValidator.split(address),
              let port = NWEndpoint.Port(rawValue: parts.port) else {
            return HubProbeResult(reachable: false, message: "Hub 地址格式无效")
        }

        return await withCheckedContinuation { continuation in
            let queue = DispatchQueue(label: "TeamChatLauncher.HubProbe")
            let connection = NWConnection(host: NWEndpoint.Host(parts.host), port: port, using: .tcp)
            let completionState = HubProbeCompletionState()

            @Sendable func finish(_ result: HubProbeResult) {
                guard completionState.claim() else { return }
                connection.cancel()
                continuation.resume(returning: result)
            }

            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    finish(HubProbeResult(reachable: true, message: "Hub 可达"))
                case .failed(let error):
                    finish(HubProbeResult(reachable: false, message: error.localizedDescription))
                case .cancelled:
                    break
                default:
                    break
                }
            }

            queue.asyncAfter(deadline: .now() + timeout) {
                finish(HubProbeResult(reachable: false, message: "连接超时"))
            }
            connection.start(queue: queue)
        }
    }
}
