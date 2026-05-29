import Foundation

enum HubAddressValidator {
    static func isValid(_ value: String) -> Bool {
        let parts = value.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2, !parts[0].isEmpty, let port = Int(parts[1]) else {
            return false
        }
        return port >= 1 && port <= 65_535 && !parts[0].contains(" ")
    }

    static func split(_ value: String) -> (host: String, port: UInt16)? {
        guard isValid(value) else { return nil }
        let parts = value.split(separator: ":", omittingEmptySubsequences: false)
        guard let port = UInt16(parts[1]) else { return nil }
        return (String(parts[0]), port)
    }
}
