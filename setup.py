from setuptools import setup, find_packages

setup(
    name="opengrab",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    package_data={"opengrab": ["ui/static/*"]},
    entry_points={
        'console_scripts': [
            'opengrab=opengrab.cli:main',
        ],
    },
    install_requires=[
        "httpx[http2]",
        "curl_cffi>=0.16.3",
        "pywebview>=6.2.1",
        "pystray>=0.19.5",
        "Pillow>=10.0",
        "pydantic>=2.14",
        "bs4",
        "lxml",
        "aiofiles",
        "yt-dlp>=2026.10.21",
        "py7zr>=1.0",
        "rarfile>=4.2",
        "ddddocr>=1.6.1",
        "pywin32-ctypes",
        "rich"
    ],
    python_requires=">=3.12",
)
