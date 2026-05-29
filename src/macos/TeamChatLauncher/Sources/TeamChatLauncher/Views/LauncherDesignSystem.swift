import SwiftUI

struct LauncherCard<Content: View>: View {
    let title: String
    let systemImage: String
    let content: Content

    init(title: String, systemImage: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.systemImage = systemImage
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: systemImage)
                .font(.headline)
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.secondary.opacity(0.18))
        )
    }
}

struct StatusBadge: View {
    let label: String
    let status: String

    var body: some View {
        Label(label, systemImage: icon)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .foregroundStyle(color)
            .background(color.opacity(0.12), in: Capsule())
    }

    private var color: Color {
        switch status {
        case "done", "ok", "running", "connected": return .green
        case "failed", "error": return .red
        case "skippable": return .secondary
        default: return .orange
        }
    }

    private var icon: String {
        switch status {
        case "done", "ok", "running", "connected": return "checkmark.circle.fill"
        case "failed", "error": return "xmark.octagon.fill"
        case "skippable": return "forward.circle.fill"
        default: return "exclamationmark.triangle.fill"
        }
    }
}
