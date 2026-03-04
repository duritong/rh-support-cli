from setuptools import setup, find_packages

setup(
    name="rh-support-cli",
    version="0.1.0",
    description="Red Hat Support Case CLI Tool",
    author="Your Name",
    author_email="your.email@example.com",
    packages=find_packages(),
    scripts=["rh-support-cli.py"],
    install_requires=["requests", "pyyaml", "jinja2", "dateparser", "argcomplete"],
    entry_points={
        "console_scripts": [
            "rh-support-cli=rh_support_lib.main:main",
        ],
    },
)
