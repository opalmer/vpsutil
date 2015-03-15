from distutils.core import setup

requires = ["requests"]

try:
    import configparser
except ImportError:
    requires.append("configparser")

setup(
    name="vpsutil",
    version="0.0.0",
    packages=["vpsutil"],
    install_requires=requires,
    license="MIT"
)
