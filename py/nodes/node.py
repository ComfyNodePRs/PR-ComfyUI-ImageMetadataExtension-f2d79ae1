import json
import os
import re
from datetime import datetime

from PIL import Image
from PIL.PngImagePlugin import PngInfo
import numpy as np

import folder_paths
from comfy.cli_args import args

from .base import BaseNode
from ..capture import Capture
from .. import hook
from ..trace import Trace
from ..defs.combo import SAMPLER_SELECTION_METHOD


# refer. https://github.com/comfyanonymous/ComfyUI/blob/38b7ac6e269e6ecc5bdd6fefdfb2fb1185b09c9d/nodes.py#L1411
class SaveImageWithMetaData(BaseNode):
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""
        self.compress_level = 4

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "The images to save."}),
                "filename_prefix": ("STRING", {"default": "ComfyUI", "tooltip": "The prefix for the file to save. This may include formatting information such as %date:yyyy-MM-dd% or %Empty Latent Image.width% to include values from nodes."})
            },
            "optional": {
                "extra_metadata": ("EXTRA_METADATA", {
                    "tooltip": "Additional metadata to be included with the saved image. This can contain key-value pairs for extra information."
                }),
                "save_prompt": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "If true, includes positive and negative prompts in the metadata."
                }),
            },
            "hidden": {
                "prompt": "PROMPT", 
                "extra_pnginfo": "EXTRA_PNGINFO"
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"

    OUTPUT_NODE = True

    DESCRIPTION = "Saves the input images with metadata to your ComfyUI output directory."

    pattern_format = re.compile(r"(%[^%]+%)")

    def save_images(self, images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None, extra_metadata={}, save_prompt=True):
        pnginfo_dict = self.generate_metadata(extra_metadata, save_prompt)

        filename_prefix += self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(filename_prefix, self.output_dir, images[0].shape[1], images[0].shape[0])
        results = list()
        for (batch_number, image) in enumerate(images):
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            metadata = self.prepare_pnginfo(pnginfo_dict, batch_number, len(images), prompt, extra_pnginfo) if not args.disable_metadata else None

            filename_with_batch_num = filename.replace("%batch_num%", str(batch_number))
            file = f"{filename_with_batch_num}_{counter:05}_.png"
            img.save(os.path.join(full_output_folder, file), pnginfo=metadata, compress_level=self.compress_level)
            results.append({
                "filename": file,
                "subfolder": subfolder,
                "type": self.type
            })
            counter += 1

        return { "ui": { "images": results } }

    def generate_metadata(self, extra_metadata, save_prompt):
        """
        Generates PNG metadata by merging extra metadata with base information.
        """
        pnginfo_dict = self.gen_pnginfo(SAMPLER_SELECTION_METHOD[0], 0, False, save_prompt)
        pnginfo_dict.update({k: v.replace(",", "/") for k, v in extra_metadata.items() if k and v})
        return pnginfo_dict


    def prepare_pnginfo(self, pnginfo_dict, batch_number, total_images, prompt, extra_pnginfo):
        metadata = PngInfo()
        pnginfo_copy = pnginfo_dict.copy()

        if total_images > 1:
            pnginfo_copy["Batch index"] = batch_number
            pnginfo_copy["Batch size"] = total_images

        parameters = Capture.gen_parameters_str(pnginfo_copy)
        metadata.add_text("parameters", parameters)

        if prompt is not None:
            metadata.add_text("prompt", json.dumps(prompt))

        if extra_pnginfo and isinstance(extra_pnginfo, dict):
            for k, v in extra_pnginfo.items():
                metadata.add_text(k, json.dumps(v))

        return metadata

    @classmethod
    def gen_pnginfo(cls, sampler_selection_method, sampler_selection_node_id, save_civitai_sampler, save_prompt):
        # get all node inputs
        inputs = Capture.get_inputs()

        # get sampler node before this node
        trace_tree_from_this_node = Trace.trace(hook.current_save_image_node_id, hook.current_prompt)
        inputs_before_this_node = Trace.filter_inputs_by_trace_tree(inputs, trace_tree_from_this_node)

        sampler_node_id = Trace.find_sampler_node_id(trace_tree_from_this_node, sampler_selection_method, sampler_selection_node_id)

        # get inputs before sampler node
        trace_tree_from_sampler_node = Trace.trace(sampler_node_id, hook.current_prompt)
        inputs_before_sampler_node = Trace.filter_inputs_by_trace_tree(inputs, trace_tree_from_sampler_node)

        # generate PNGInfo from inputs
        return Capture.gen_pnginfo_dict(inputs_before_sampler_node, inputs_before_this_node, save_civitai_sampler, save_prompt)

    @classmethod
    def format_filename(cls, filename, pnginfo_dict):
        date_table = cls.get_date_table()

        def replace_segment(segment):
            key, *parts = segment.replace("%", "").split(":")
            if key == "seed":
                return pnginfo_dict.get("Seed", "")
            elif key == "width":
                return pnginfo_dict.get("Size", "x").split("x")[0]
            elif key == "height":
                return pnginfo_dict.get("Size", "x").split("x")[1]
            elif key == "pprompt":
                prompt = pnginfo_dict.get("Positive prompt", "").replace("\n", " ")
                return prompt[:int(parts[0])] if parts else prompt.strip()
            elif key == "nprompt":
                prompt = pnginfo_dict.get("Negative prompt", "").replace("\n", " ")
                return prompt[:int(parts[0])] if parts else prompt.strip()
            elif key == "model":
                model = os.path.splitext(os.path.basename(pnginfo_dict.get("Model", "")))[0]
                return model[:int(parts[0])] if parts else model
            elif key == "date":
                return cls.format_date(parts, date_table)
            return ""

        return re.sub(cls.pattern_format, lambda m: replace_segment(m.group(0)), filename)

    @staticmethod
    def get_date_table():
        now = datetime.now()
        return {
            "yyyy": now.year,
            "MM": now.month,
            "dd": now.day,
            "hh": now.hour,
            "mm": now.minute,
            "ss": now.second,
        }

    @staticmethod
    def format_date(parts, date_table):
        date_format = parts[0] if parts else "yyyyMMddhhmmss"
        for k, v in date_table.items():
            date_format = date_format.replace(k, str(v).zfill(len(k)))
        return date_format


class CreateExtraMetaData(BaseNode):
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "key1": ("STRING", {"default": "", "multiline": False}),
                "value1": ("STRING", {"default": "", "multiline": False}),
            },
            "optional": {
                "key2": ("STRING", {"default": "", "multiline": False}),
                "value2": ("STRING", {"default": "", "multiline": False}),
                "key3": ("STRING", {"default": "", "multiline": False}),
                "value3": ("STRING", {"default": "", "multiline": False}),
                "key4": ("STRING", {"default": "", "multiline": False}),
                "value4": ("STRING", {"default": "", "multiline": False}),
                "extra_metadata": ("EXTRA_METADATA",),
            },
        }

    RETURN_TYPES = ("EXTRA_METADATA",)
    FUNCTION = "create_extra_metadata"

    def create_extra_metadata(
        self,
        extra_metadata={},
        key1="",
        value1="",
        key2="",
        value2="",
        key3="",
        value3="",
        key4="",
        value4="",
    ):
        extra_metadata.update(
            {
                key1: value1,
                key2: value2,
                key3: value3,
                key4: value4,
            }
        )
        return (extra_metadata,)
