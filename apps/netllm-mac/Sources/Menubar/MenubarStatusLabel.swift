import SwiftUI

struct MenubarStatusLabel: View {
    @Bindable var model: MenubarAppModel

    var body: some View {
        let _ = model.updateRevision
        return ZStack(alignment: .topTrailing) {
            Group {
                if let icon = BrandAssets.menubarIcon() {
                    Image(nsImage: icon)
                        .renderingMode(.template)
                } else {
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                }
            }
            .accessibilityLabel(AppBranding.displayName)

            if model.hasUpdateBadge {
                Circle()
                    .fill(DesignTokens.warn)
                    .frame(width: 7, height: 7)
                    .offset(x: 3, y: -3)
                    .accessibilityHidden(true)
            }
        }
    }
}
