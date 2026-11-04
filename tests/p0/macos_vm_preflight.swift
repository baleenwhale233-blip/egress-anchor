// Read-only Virtualization preflight. Does not create, install, or start a VM.
import Foundation
import Virtualization

@main
struct MacOSVMPreflight {
    static func main() async {
        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let resources = try? root.resourceValues(forKeys: [.volumeAvailableCapacityKey])
        var report: [String: Any] = [
            "checked_at": ISO8601DateFormatter().string(from: Date()),
            "host_os": ProcessInfo.processInfo.operatingSystemVersionString,
            "physical_memory_bytes": ProcessInfo.processInfo.physicalMemory,
            "virtualization_supported": VZVirtualMachine.isSupported,
            "available_disk_bytes": resources?.volumeAvailableCapacity ?? 0,
            "image_downloaded": false,
            "vm_created": false
        ]
        #if arch(arm64)
        report["apple_silicon"] = true
        #else
        report["apple_silicon"] = false
        #endif
        if CommandLine.arguments.contains("--metadata") {
            do {
                let image = try await VZMacOSRestoreImage.latestSupported
                let version = image.operatingSystemVersion
                report["restore_image"] = [
                    "version": "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)",
                    "build": image.buildVersion,
                    "supported": image.isSupported,
                    "url": image.url.absoluteString
                ]
                if let requirements = image.mostFeaturefulSupportedConfiguration {
                    report["minimum_memory_bytes"] = requirements.minimumSupportedMemorySize
                    report["minimum_cpu_count"] = requirements.minimumSupportedCPUCount
                    report["hardware_model_supported"] = requirements.hardwareModel.isSupported
                }
                var request = URLRequest(url: image.url)
                request.httpMethod = "HEAD"
                request.timeoutInterval = 12
                let (_, response) = try await URLSession.shared.data(for: request)
                let imageBytes = max(0, response.expectedContentLength)
                report["restore_image_bytes"] = imageBytes
                // Workspace budget for archive, installed OS, and test/rollback headroom.
                let budget = imageBytes + 40 * 1024 * 1024 * 1024
                report["recommended_free_bytes_estimate"] = budget
                report["disk_budget_satisfied"] = Int64(resources?.volumeAvailableCapacity ?? 0) >= budget
            } catch {
                report["metadata_error"] = error.localizedDescription
            }
        }
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]),
           let text = String(data: data, encoding: .utf8) {
            print(text)
        }
    }
}
