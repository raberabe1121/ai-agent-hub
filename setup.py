from setuptools import find_packages, setup


setup(
    name="ai-agent-os",
    version="0.4.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=["fastembed>=0.3.0", "sqlite-vec>=0.1.0", "pypdf>=4.0.0", "python-docx>=1.1.0"],
    entry_points={
        "console_scripts": [
            "hub=ai_agent_hub.cli:main",
        ],
    },
)
