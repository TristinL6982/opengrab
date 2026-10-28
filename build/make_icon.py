#!/usr/bin/env python3
"""
Script to generate OpenGrab icon for windowed executable.
"""

import os
from pathlib import Path
from PIL import Image, ImageDraw

# Create assets directory if it doesn't exist
assets_dir = Path("src/opengrab/assets")
assets_dir.mkdir(parents=True, exist_ok=True)

def create_icon():
    # Create a list of sizes to generate
    sizes = [16, 24, 32, 48, 64, 128, 256]
    
    # Create the icon with blue rounded square and white arrow
    icon_images = []
    
    for size in sizes:
        # Create a new image with RGBA mode for transparency
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw blue rounded square background
        draw.rounded_rectangle(
            [(size//8, size//8), (size - size//8, size - size//8)],
            radius=size//4,
            fill="#38bdf8"  # Blue color
        )
        
        # Draw white downward arrow
        arrow_width = size // 4
        arrow_height = size // 2
        arrow_x = size // 2
        arrow_y = size // 2
        
        # Arrow head (downward)
        draw.polygon([
            (arrow_x - arrow_width//2, arrow_y - arrow_height//4),
            (arrow_x + arrow_width//2, arrow_y - arrow_height//4),
            (arrow_x, arrow_y + arrow_height//2)
        ], fill="#ffffff")  # White arrow
        
        # Arrow shaft
        draw.rectangle([
            (arrow_x - arrow_width//8, arrow_y - arrow_height//4), 
            (arrow_x + arrow_width//8, arrow_y + arrow_height//4)
        ], fill="#ffffff")
        
        icon_images.append(img)
    
    # Save individual PNG files for each size
    for i, img in enumerate(icon_images):
        size = sizes[i]
        png_path = Path(f"build/opengrab_{size}.png")
        img.save(png_path, format='PNG')
        print(f"Icon saved: {png_path}")
    
    # Save 256x256 PNG for assets
    icon_256 = icon_images[6]  # 256px size
    png_path = Path("src/opengrab/assets/icon.png")
    icon_256.save(png_path, format='PNG')
    
    print(f"Asset saved: {png_path}")

if __name__ == "__main__":
    create_icon()