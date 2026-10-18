#!/usr/bin/env python3
"""
Packages the plugin.video.tvh.youtube.channels directory into a clean distributable zip file.
"""

import os
import zipfile

def package_addon():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    addon_dir = os.path.join(base_dir, "plugin.video.tvh.youtube.channels")
    output_zip = os.path.join(base_dir, "plugin.video.tvh.youtube.channels.zip")
    
    if not os.path.exists(addon_dir):
        print(f"Error: Addon folder {addon_dir} not found.")
        return
        
    print(f"Packaging {addon_dir} -> {output_zip}...")
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(addon_dir):
            for file in files:
                if file.endswith((".pyc", ".pyo", ".git", ".DS_Store")):
                    continue
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, base_dir)
                z.write(file_path, arcname)
                
    print(f"Successfully created {output_zip} ({os.path.getsize(output_zip)} bytes).")

if __name__ == "__main__":
    package_addon()
