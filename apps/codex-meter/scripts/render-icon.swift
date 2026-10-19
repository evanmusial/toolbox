#!/usr/bin/env swift

import AppKit

guard CommandLine.arguments.count == 3 else {
    fputs("usage: render-icon.swift INPUT.svg OUTPUT.png\n", stderr)
    exit(2)
}

let inputURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
let pixels = 1024

guard let source = NSImage(contentsOf: inputURL) else {
    fputs("could not load SVG: \(inputURL.path)\n", stderr)
    exit(3)
}

guard let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: pixels,
    pixelsHigh: pixels,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
) else {
    fputs("could not allocate icon bitmap\n", stderr)
    exit(4)
}

bitmap.size = NSSize(width: pixels, height: pixels)
guard let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
    fputs("could not create icon graphics context\n", stderr)
    exit(5)
}

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context
context.cgContext.clear(CGRect(x: 0, y: 0, width: pixels, height: pixels))
context.imageInterpolation = .high
source.draw(
    in: NSRect(x: 0, y: 0, width: pixels, height: pixels),
    from: .zero,
    operation: .sourceOver,
    fraction: 1
)
context.flushGraphics()
NSGraphicsContext.restoreGraphicsState()

guard let png = bitmap.representation(using: .png, properties: [:]) else {
    fputs("could not encode icon PNG\n", stderr)
    exit(6)
}

try png.write(to: outputURL, options: .atomic)
