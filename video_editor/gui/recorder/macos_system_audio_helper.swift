import AVFoundation
import CoreGraphics
import CoreMedia
import Dispatch
import Foundation
import ScreenCaptureKit

private enum ToolError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case let .message(message):
            return message
        }
    }
}

struct Options {
    let displayIndex: Int
    let outputURL: URL
    let captureSystemAudio: Bool
    let captureMicrophone: Bool
    let microphoneName: String?
    let frameRate: Int
    let sampleRate: Int
    let channelCount: Int

    static func parse(arguments: [String]) throws -> Options {
        var values: [String: String] = [:]
        var index = 0

        while index < arguments.count {
            let key = arguments[index]
            guard key.hasPrefix("--") else {
                throw ToolError.message("Unexpected argument: \(key)")
            }

            let valueIndex = index + 1
            guard valueIndex < arguments.count else {
                throw ToolError.message("Missing value for argument: \(key)")
            }

            values[key] = arguments[valueIndex]
            index += 2
        }

        guard let outputPath = values["--output"], !outputPath.isEmpty else {
            throw ToolError.message("Missing required --output path")
        }

        return Options(
            displayIndex: Int(values["--display-index"] ?? "0") ?? 0,
            outputURL: URL(fileURLWithPath: outputPath),
            captureSystemAudio: Self.parseBool(values["--capture-system-audio"], defaultValue: true),
            captureMicrophone: Self.parseBool(values["--capture-microphone"], defaultValue: false),
            microphoneName: values["--microphone-name"],
            frameRate: max(Int(values["--frame-rate"] ?? "30") ?? 30, 1),
            sampleRate: max(Int(values["--sample-rate"] ?? "48000") ?? 48000, 8000),
            channelCount: max(Int(values["--channel-count"] ?? "2") ?? 2, 1)
        )
    }

    private static func parseBool(_ raw: String?, defaultValue: Bool) -> Bool {
        guard let raw else {
            return defaultValue
        }

        switch raw.lowercased() {
        case "1", "true", "yes", "on":
            return true
        case "0", "false", "no", "off":
            return false
        default:
            return defaultValue
        }
    }
}

private func emit(event: String, payload: [String: Any] = [:]) {
    var message = payload
    message["event"] = event

    guard JSONSerialization.isValidJSONObject(message),
          let data = try? JSONSerialization.data(withJSONObject: message, options: []),
          let newline = "\n".data(using: .utf8)
    else {
        return
    }

    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(newline)
}

@available(macOS 15.0, *)
private func fetchShareableContent() async throws -> SCShareableContent {
    try await withCheckedThrowingContinuation { continuation in
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) {
            content,
            error in
            if let error {
                continuation.resume(throwing: error)
                return
            }

            guard let content else {
                continuation.resume(throwing: ToolError.message("Shareable content list was empty"))
                return
            }

            continuation.resume(returning: content)
        }
    }
}

@available(macOS 15.0, *)
private func startCapture(stream: SCStream) async throws {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
        stream.startCapture { error in
            if let error {
                continuation.resume(throwing: error)
            } else {
                continuation.resume()
            }
        }
    }
}

@available(macOS 15.0, *)
private func stopCapture(stream: SCStream) async throws {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
        stream.stopCapture { error in
            if let error {
                continuation.resume(throwing: error)
            } else {
                continuation.resume()
            }
        }
    }
}

private func requestMicrophoneAccessIfNeeded() async -> Bool {
    let status = AVCaptureDevice.authorizationStatus(for: .audio)
    switch status {
    case .authorized:
        return true
    case .notDetermined:
        return await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
    default:
        return false
    }
}

private func selectMicrophone(named preferredName: String?) -> AVCaptureDevice? {
    let devices = AVCaptureDevice.DiscoverySession(
        deviceTypes: [.microphone, .external],
        mediaType: .audio,
        position: .unspecified
    ).devices

    guard let preferredName, !preferredName.isEmpty else {
        return devices.first ?? AVCaptureDevice.default(for: .audio)
    }

    if let exactMatch = devices.first(where: { $0.localizedName.caseInsensitiveCompare(preferredName) == .orderedSame }) {
        return exactMatch
    }

    let loweredName = preferredName.lowercased()
    if let partialMatch = devices.first(where: { $0.localizedName.lowercased().contains(loweredName) || loweredName.contains($0.localizedName.lowercased()) }) {
        return partialMatch
    }

    return AVCaptureDevice.default(for: .audio)
}

@available(macOS 15.0, *)
final class RecorderApp: NSObject, SCRecordingOutputDelegate, SCStreamDelegate {
    private let options: Options
    private var stream: SCStream?
    private var recordingOutput: SCRecordingOutput?
    private var stopping = false
    private var hasExited = false

    init(options: Options) {
        self.options = options
        super.init()
    }

    func start() {
        listenForCommands()
        Task {
            await run()
        }
    }

    private func run() async {
        do {
            guard CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() else {
                throw ToolError.message("macOS screen and system audio recording access was denied")
            }

            let includeMicrophone = try await resolveMicrophoneCapture()
            let shareableContent = try await fetchShareableContent()

            guard options.displayIndex >= 0, options.displayIndex < shareableContent.displays.count else {
                throw ToolError.message("Display index \(options.displayIndex) is out of range")
            }

            let display = shareableContent.displays[options.displayIndex]
            let filter = SCContentFilter(display: display, excludingWindows: [])

            let streamConfiguration = SCStreamConfiguration()
            streamConfiguration.width = size_t(CGDisplayPixelsWide(display.displayID))
            streamConfiguration.height = size_t(CGDisplayPixelsHigh(display.displayID))
            streamConfiguration.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(options.frameRate))
            streamConfiguration.queueDepth = 8
            streamConfiguration.showsCursor = true
            streamConfiguration.capturesAudio = options.captureSystemAudio
            streamConfiguration.sampleRate = options.sampleRate
            streamConfiguration.channelCount = options.channelCount
            streamConfiguration.excludesCurrentProcessAudio = false
            streamConfiguration.captureMicrophone = includeMicrophone

            if includeMicrophone, let microphone = selectMicrophone(named: options.microphoneName) {
                streamConfiguration.microphoneCaptureDeviceID = microphone.uniqueID
                if let preferredName = options.microphoneName,
                   microphone.localizedName.caseInsensitiveCompare(preferredName) != .orderedSame
                {
                    emit(
                        event: "warning",
                        payload: [
                            "message": "Selected microphone '\(preferredName)' was not found exactly. Using '\(microphone.localizedName)' instead."
                        ]
                    )
                }
            }

            try prepareOutputLocation()

            let recordingConfiguration = SCRecordingOutputConfiguration()
            recordingConfiguration.outputURL = options.outputURL
            if recordingConfiguration.availableVideoCodecTypes.contains(.h264) {
                recordingConfiguration.videoCodecType = .h264
            }
            if recordingConfiguration.availableOutputFileTypes.contains(.mp4) {
                recordingConfiguration.outputFileType = .mp4
            }

            let stream = SCStream(filter: filter, configuration: streamConfiguration, delegate: self)
            let recordingOutput = SCRecordingOutput(configuration: recordingConfiguration, delegate: self)
            try stream.addRecordingOutput(recordingOutput)

            self.stream = stream
            self.recordingOutput = recordingOutput

            try await startCapture(stream: stream)
        } catch {
            failAndExit(error.localizedDescription)
        }
    }

    private func resolveMicrophoneCapture() async throws -> Bool {
        guard options.captureMicrophone else {
            return false
        }

        let granted = await requestMicrophoneAccessIfNeeded()
        if granted {
            return true
        }

        emit(
            event: "warning",
            payload: ["message": "Microphone access was denied. Continuing with system audio only."]
        )
        return false
    }

    private func prepareOutputLocation() throws {
        let directory = options.outputURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: options.outputURL.path) {
            try FileManager.default.removeItem(at: options.outputURL)
        }
    }

    private func listenForCommands() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else {
                return
            }

            while let line = readLine(strippingNewline: true) {
                if line == "stop" {
                    Task {
                        await self.requestStop()
                    }
                    return
                }
            }

            Task {
                await self.requestStop()
            }
        }
    }

    private func requestStop() async {
        if stopping {
            return
        }

        stopping = true
        guard let stream else {
            finishAndExit()
            return
        }

        do {
            try await stopCapture(stream: stream)
        } catch {
            failAndExit(error.localizedDescription)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        if stopping {
            return
        }

        failAndExit(error.localizedDescription)
    }

    func recordingOutputDidStartRecording(_ recordingOutput: SCRecordingOutput) {
        emit(event: "started")
    }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        failAndExit(error.localizedDescription)
    }

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        finishAndExit()
    }

    private func finishAndExit() {
        guard !hasExited else {
            return
        }

        hasExited = true
        emit(
            event: "finished",
            payload: ["output_path": options.outputURL.path]
        )
        Foundation.exit(0)
    }

    private func failAndExit(_ message: String) {
        guard !hasExited else {
            return
        }

        hasExited = true
        emit(event: "error", payload: ["message": message])
        Foundation.exit(1)
    }
}

@main
struct MacOSSystemAudioRecorderTool {
    static func main() {
        do {
            let options = try Options.parse(arguments: Array(CommandLine.arguments.dropFirst()))
            guard #available(macOS 15.0, *) else {
                throw ToolError.message("Native macOS system audio capture requires macOS 15 or later")
            }

            let app = RecorderApp(options: options)
            app.start()
            dispatchMain()
        } catch {
            emit(event: "error", payload: ["message": error.localizedDescription])
            Foundation.exit(1)
        }
    }
}
