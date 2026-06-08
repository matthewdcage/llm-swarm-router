import SwiftUI

struct BrandedHeader: View {
    var subtitle: String?

    var body: some View {
        HStack(spacing: 12) {
            BrandImageView(size: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(AppBranding.displayName)
                    .font(.headline)
                Text(subtitle ?? AppBranding.tagline)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
    }
}
