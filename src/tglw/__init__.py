"""Product identity, kept in this one place: NAME is the name in prose, PACKAGE the pip name (the import
package stays tglw), COMMAND the console command, HOME_ENV the variable naming the document library.
pyproject.toml reads VERSION from here; its [project] name must equal PACKAGE and its Repository url REPO."""

NAME = "Vestigium"
PACKAGE = "vestigium"
COMMAND = "vestigium"
HOME_ENV = "VESTIGIUM_HOME"
VERSION = "0.1.1"
REPO = "https://github.com/Yonagi043/vestigium"
