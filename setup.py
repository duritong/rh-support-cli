from setuptools import find_packages, setup

setup(
    name="rh-support-cli",
    version="0.1.0",
    description="Red Hat Support Case CLI Tool",
    author="Your Name",
    author_email="your.email@example.com",
    packages=find_packages(exclude=("tests", "tests.*")),
    install_requires=[
        "requests",
        "requests-toolbelt",
        "pyyaml",
        "jinja2",
        "dateparser",
        "argcomplete",
        "rich",
        "textual",
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
