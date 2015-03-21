from distutils.core import setup

requires = ["requests", "paramiko"]

try:
    import configparser
except ImportError:
    requires.append("configparser")

setup(
    name="vpsutil",
    version="0.0.0",
    license="MIT",
    packages=["vpsutil"],
    install_requires=requires,
    entry_points={
        "console_scripts": [
            "ocean = vpsutil.command:ocean"
        ]
    }
)
