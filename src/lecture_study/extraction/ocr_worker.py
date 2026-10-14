"""Native OCR via Apple's Vision framework: recognizes pt-BR text on slide
frames extracted by media_workers.extract_frames.

Uses pyobjc-framework-Quartz to decode the PNG into a CGImage handle and
pyobjc-framework-Vision to run VNRecognizeTextRequest against it. See
docs/decisions/0001-apple-silicon-quartz.md for why Quartz, not CoreImage,
supplies that handle.
"""

import json
import sys
from pathlib import Path
from typing import Any

import objc
from Foundation import NSURL
from Quartz import CGImageSourceCreateImageAtIndex, CGImageSourceCreateWithURL
from Vision import (
    VNImageRequestHandler,
    VNRecognizeTextRequest,
    VNRequestTextRecognitionLevelAccurate,
)

_RECOGNITION_LANGUAGES = ["pt-BR"]
OCR_FAILURE_EXIT_CODE = 2


class OcrError(RuntimeError):
    """Raised when a slide frame cannot be decoded or Vision OCR fails."""


def extract_text_from_image(image_path: Path) -> str:
    """Run Apple Vision OCR (accurate, pt-BR) on image_path.

    Wrapped in its own autorelease pool so the CoreFoundation/Objective-C
    objects Vision allocates per call (image source, CGImage, request,
    observations) are drained immediately instead of accumulating across a
    tight loop over thousands of slide frames.
    """
    with objc.autorelease_pool():
        cg_image = _load_cg_image(image_path)
        request = _build_text_request()
        # macOS 27 rejects an empty options dict (NSInvalidArgumentException); None means no options.
        handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
        success, error = handler.performRequests_error_([request], None)
        if not success:
            raise OcrError(f"Vision OCR request failed for {image_path}: {error}")
        return _join_recognized_lines(request.results())


def _load_cg_image(image_path: Path) -> Any:
    # Untyped boundary: pyobjc's Quartz bindings ship no stubs, so every
    # value here is Any by construction.
    url = NSURL.fileURLWithPath_(str(image_path))
    source = CGImageSourceCreateWithURL(url, None)
    if source is None:
        raise OcrError(f"cannot decode image source: {image_path}")
    cg_image = CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        raise OcrError(f"cannot decode CGImage: {image_path}")
    return cg_image


def _build_text_request() -> Any:
    request = VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(_RECOGNITION_LANGUAGES)
    request.setUsesLanguageCorrection_(True)
    return request


def _join_recognized_lines(observations: Any) -> str:
    lines: list[str] = []
    for observation in observations or []:
        candidates = observation.topCandidates_(1)
        if candidates:
            lines.append(str(candidates[0].string()))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """Child entry point: print the OCR text of argv[0] as a JSON object."""
    try:
        text = extract_text_from_image(Path(argv[0]))
    except OcrError as error:
        print(error, file=sys.stderr)
        return OCR_FAILURE_EXIT_CODE
    print(json.dumps({"text": text}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
