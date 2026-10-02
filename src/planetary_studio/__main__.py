import argparse
import json
import os
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Planetary Studio — capture, prepare, stack, sharpen")
    parser.add_argument("--version", action="version", version="Planetary Studio 0.1.0")
    parser.add_argument(
        "--self-test", action="store_true", help="Verify the complete pipeline and desktop startup"
    )
    parser.add_argument("--report", help="Write self-test results to JSON")
    sub = parser.add_subparsers(dest="command")
    demo = sub.add_parser("demo", help="Generate a realistic test recording")
    demo.add_argument("output")
    demo.add_argument("--frames", type=int, default=60)
    stack = sub.add_parser("stack", help="Process a recording without opening the desktop")
    stack.add_argument("input")
    stack.add_argument("output")
    stack.add_argument("--keep", type=float, default=25)
    stack.add_argument("--bayer", default="AUTO", choices=["AUTO", "MONO", "RGGB", "GRBG", "GBRG", "BGGR"])
    stack.add_argument("--surface", action="store_true")
    stack.add_argument("--global-only", action="store_true")
    stack.add_argument("--ap-size", type=int, default=64)
    stack.add_argument("--crop", type=int, nargs=2, default=[0, 0], metavar=("WIDTH", "HEIGHT"))
    stack.add_argument("--dark", default="")
    stack.add_argument("--flat", default="")
    stack.add_argument("--scale", type=float, choices=[1, 1.5, 2], default=1)
    probe = sub.add_parser("probe-camera", help="Capture raw frames from a directly connected UVC camera")
    probe.add_argument("name", help="Camera name substring")
    probe.add_argument("output", help="New SER recording path")
    probe.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from .selftest import run_self_test

            print(json.dumps(run_self_test(args.report), indent=2))
            return 0
        if args.command == "demo":
            from .selftest import generate_demo

            if not 1 <= args.frames <= 10000:
                raise ValueError("Demo frame count must be 1–10000.")
            generate_demo(args.output, args.frames)
            print("Demo recording saved:", args.output)
        elif args.command == "stack":
            from .processing import StackOptions, stack_source

            options = StackOptions(
                keep_percent=args.keep,
                bayer=args.bayer,
                center_object=not args.surface,
                local_alignment=not args.global_only,
                alignment_size=args.ap_size,
                crop_width=args.crop[0],
                crop_height=args.crop[1],
                dark_path=args.dark,
                flat_path=args.flat,
                scale=args.scale,
            )
            result = stack_source(
                args.input, options, lambda percent, text: print(f"{percent:.0f}% {text}", flush=True)
            )
            result.save(args.output)
        elif args.command == "probe-camera":
            import time
            from .cameras.uvc import discover, UvcCamera
            from .ser import SerWriter

            matches = [d for d in discover() if args.name.casefold() in d.name.casefold()]
            if len(matches) != 1:
                raise ValueError(f"Expected one matching camera, found {len(matches)}.")
            if not 0.1 <= args.seconds <= 60:
                raise ValueError("Probe duration must be .1–60 seconds.")
            camera = UvcCamera(matches[0])
            writer = None
            try:
                mode = camera.modes()[0]
                camera.start(mode)
                end = time.monotonic() + args.seconds
                while time.monotonic() < end:
                    frame = camera.read(500)
                    if frame is None:
                        continue
                    if writer is None:
                        writer = SerWriter(args.output, frame.pixels.shape, frame.bits, frame.pattern)
                    writer.write(frame.pixels)
                if writer is None:
                    raise ValueError("Camera opened but delivered no frames.")
                print(
                    json.dumps(
                        {
                            "camera": matches[0].name,
                            "mode": mode.label,
                            "frames": writer.count,
                            "output": args.output,
                        }
                    )
                )
            finally:
                if writer:
                    writer.close()
                camera.close()
        else:
            from .app import run

            return run()
        return 0
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
