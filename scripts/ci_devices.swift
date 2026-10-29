// Capability inventory only: no models, weights or inference are executed.
import CoreML
import Foundation
import Metal

var coreMLDevices: [String] = []
if #available(macOS 14.0, *) {
    for device in MLComputeDevice.allComputeDevices {
        switch device {
        case .cpu(_): coreMLDevices.append("cpu")
        case .gpu(_): coreMLDevices.append("gpu")
        case .neuralEngine(_): coreMLDevices.append("neural_engine")
        @unknown default: coreMLDevices.append("unknown")
        }
    }
}

let report: [String: Any] = [
    "schema": "local-annotation.compute-device-inventory.v1",
    "coreml_devices": coreMLDevices,
    "neural_engine_accessible": coreMLDevices.contains("neural_engine"),
    "metal_devices": MTLCopyAllDevices().map { $0.name },
    "inference_tested": false,
    "note": "Device visibility does not establish MPS or Neural Engine inference support."
]
let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
print(String(decoding: data, as: UTF8.self))
