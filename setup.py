#!/usr/bin/env python3
"""Setup configuration for kalshi-market-maker-bot."""

import pathlib
from setuptools import find_packages, setup

here = pathlib.Path(__file__).parent
readme_path = here / "README.md"
long_description = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

setup(
    name="kalshi-market-maker-bot",
    version="1.0.0",
    description="Institutional Avellaneda-Stoikov Market Making Engine & CFTC Fee Optimizer for Kalshi Prediction Markets",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="NaxosData Quant Engineering & William Gonda",
    author_email="research@naxosdata.com",
    url="https://github.com/nexosdata/kalshi-market-maker-bot",
    packages=find_packages(include=["kalshi_mm", "kalshi_mm.*"]),
    python_requires=">=3.10",
    install_requires=[
        "httpx>=0.24.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "kalshi-mm=kalshi_mm.cli:main",
            "kalshi-market-maker=kalshi_mm.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Financial and Insurance Industry",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Office/Business :: Financial :: Investment",
    ],
)
