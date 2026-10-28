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
    # Create a 256x256 icon for the main asset
    icon_size = 256
    img = Image.new('RGBA', (icon_size, icon_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Draw blue rounded square background
    draw.rounded_rectangle(
        [(icon_size//8, icon_size//8), (icon_size - icon_size//8, icon_size - icon_size//8)],
        radius=icon_size//4,
        fill="#38bdf8"  # Blue color
    )
    
    # Draw white downward arrow
    arrow_width = icon_size // 4
    arrow_height = icon_size // 2
    arrow_x = icon_size // 2
    arrow_y = icon_size // 2
    
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
    
    # Save 256x256 PNG for assets  
    png_path = Path("src/opengrab/assets/icon.png")
    img.save(png_path, format='PNG')
    print(f"Asset saved: {png_path}")
    
    # Create multiple sizes for the ICO file
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon_images = []
    
    for size in sizes:
        # Create a new image with RGBA mode for transparency
        icon_img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(icon_img)
        
        # Draw blue rounded square background (scaled)
        scaled_size = size - size//4
        draw.rounded_rectangle(
            [(size//8, size//8), (size - size//8, size - size//8)],
            radius=size//4,
            fill="#38bdf8"  # Blue color
        )
        
        # Draw white downward arrow (scaled)
        scaled_arrow_width = size // 4
        scaled_arrow_height = size // 2
        scaled_arrow_x = size // 2
        scaled_arrow_y = size // 2
        
        # Arrow head (downward, scaled)
        draw.polygon([
            (scaled_arrow_x - scaled_arrow_width//2, scaled_arrow_y - scaled_arrow_height//4),
            (scaled_arrow_x + scaled_arrow_width//2, scaled_arrow_y - scaled_arrow_height//4),
            (scaled_arrow_x, scaled_arrow_y + scaled_arrow_height//2)
        ], fill="#ffffff")
        
        # Arrow shaft
        draw.rectangle([
            (scaled_arrow_x - scaled_arrow_width//8, scaled_arrow_y - scaled_arrow_height//4), 
            (scaled_arrow_x + scaled_arrow_width//8, scaled_arrow_y + scaled_arrow_height//4)
        ], fill="#ffffff")
        
        icon_images.append(icon_img)
    
    # Save the main ICO file
    ico_path = Path("build/opengrab.ico")
    icon_images[-1].save(ico_path, format='ICO', sizes=[(s,s) for s in sizes])
    print(f"Icon saved: {ico_path}")

if __name__ == "__main__":
    create_icon()