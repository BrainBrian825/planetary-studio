"""Reproduce the bundled FFDNet convolution graphs from the author's weights.

Conversion only: pip install torch==2.14.1 onnx==1.23.1 certifi
Run from the repository root: python packaging/export_ffdnet.py --cache build/ffdnet
The application uses OpenCV DNN; it does not require PyTorch or ONNX Python.
Pixel shuffle and the noise map are handled by ai_denoise.py at float precision.
"""

import argparse
import hashlib
import json
import ssl
import urllib.request
from pathlib import Path

import certifi
import onnx
import torch
from onnx import TensorProto, helper, numpy_helper

SOURCE_COMMIT = "fc1732f4a4514e42ce15e5b3a1e18c828af47a1e"
WEIGHTS = {
    "color": "99a5081e32afdaa25df3ded11aa16447556692e550906caed320db31324af5ce",
    "gray": "0b254d45dafc1ed04729b2206e0c09e5cc1e477e1d094e5a876ea45a34e5d84c",
}


def convert(cache, destination):
    cache.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"model": "FFDNet", "upstream": "https://github.com/cszn/KAIR",
                "source_commit": SOURCE_COMMIT, "license": "MIT", "variants": {}}
    context = ssl.create_default_context(cafile=certifi.where())
    for kind, digest in WEIGHTS.items():
        name = f"ffdnet_{kind}_clip"
        url = f"https://github.com/cszn/KAIR/releases/download/v1.0/{name}.pth"
        path = cache / (name + ".pth")
        if not path.exists():
            with urllib.request.urlopen(url, context=context, timeout=90) as response:
                path.write_bytes(response.read())
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Upstream weight checksum mismatch: {name}")
        weights = torch.load(path, map_location="cpu", weights_only=True)
        count, channels = (12, 3) if kind == "color" else (15, 1)
        nodes, tensors, previous = [], [], "input"
        for layer in range(count):
            prefix = f"model.{2 * layer}"
            weight, bias = weights[prefix + ".weight"], weights[prefix + ".bias"]
            tensors.extend([numpy_helper.from_array(weight.numpy(), prefix + ".weight"),
                            numpy_helper.from_array(bias.numpy(), prefix + ".bias")])
            output = "output" if layer == count - 1 else f"conv_{layer}"
            nodes.append(helper.make_node("Conv", [previous, prefix + ".weight", prefix + ".bias"],
                                          [output], kernel_shape=[3, 3], pads=[1, 1, 1, 1]))
            if layer < count - 1:
                previous = f"relu_{layer}"
                nodes.append(helper.make_node("Relu", [output], [previous]))
        graph = helper.make_graph(nodes, name,
                                  [helper.make_tensor_value_info("input", TensorProto.FLOAT,
                                                                [1, channels * 4 + 1, "height", "width"])],
                                  [helper.make_tensor_value_info("output", TensorProto.FLOAT,
                                                                [1, channels * 4, "height", "width"])],
                                  initializer=tensors)
        model = helper.make_model(graph, producer_name="Planetary Studio FFDNet converter",
                                  opset_imports=[helper.make_opsetid("", 11)], ir_version=7)
        model.doc_string = "FFDNet clipped-image weights, Kai Zhang, KAIR v1.0, MIT license."
        onnx.checker.check_model(model)
        output_path = destination / (name + ".onnx")
        onnx.save(model, output_path)
        manifest["variants"][kind] = {"weights_url": url, "weights_sha256": digest,
                                     "file": output_path.name,
                                     "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                                     "channels": channels, "convolutions": count}
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/cszn/KAIR/{SOURCE_COMMIT}/LICENSE",
                                context=context, timeout=30) as response:
        (destination / "FFDNet-LICENSE.txt").write_bytes(response.read())
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("build/ffdnet"))
    args = parser.parse_args()
    convert(args.cache, Path(__file__).resolve().parents[1] / "src/planetary_studio/assets/models")
