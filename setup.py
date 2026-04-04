from setuptools import setup, find_packages

setup(
    name="hybrid-comfyui",
    version="0.1.0",
    description="Prompt-driven automation layer for ComfyUI (hybrid: prompt + manual)",
    author="Your Name",
    packages=find_packages(include=["prompt_layer", "prompt_layer.*"]),
    include_package_data=True,
    install_requires=[],  # keep core stdlib-only
    extras_require={
        "llm": [
            "ollama>=0.3",
        ]
    },
    python_requires=">=3.9",
)
