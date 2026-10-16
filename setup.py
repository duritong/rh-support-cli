from setuptools import find_packages, setup

setup(
    name="rh-support-cli",
    version="0.1.0",
    description="Red Hat Support Case CLI Tool",
    author="Your Name",
    author_email="your.email@example.com",
    packages=find_packages(exclude=("tests", "tests.*")),
    python_requires=">=3.9",
    install_requires=[
        "requests>=2.25.0",
        "requests-toolbelt>=1.0.0",
        "pyyaml>=5.4.1",
        "jinja2>=3.0.0",
        "dateparser>=1.0.0",
        "argcomplete>=1.12.0",
        "rich>=13.3.3",
        "textual>=6.0.0",
        "markdown-it-py>=2.2.0",
    ],
    package_data={
        "rh_support_lib": ["data/*.json"],
    },
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "rh-support-cli=rh_support_lib.main:main",
        ],
    },
)
