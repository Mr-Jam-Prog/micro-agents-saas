import importlib
import inspect
import pkgutil
from typing import Dict, Any, List, Type, Optional

class PluginManager:
    def __init__(self, plugin_package: str):
        self.plugin_package = plugin_package
        self._plugins: Dict[str, Type] = {}

    def load_plugins(self):
        package = importlib.import_module(self.plugin_package)
        for _, name, is_pkg in pkgutil.iter_modules(package.__path__):
            full_name = f"{self.plugin_package}.{name}"
            module = importlib.import_module(full_name)

            # Découvrir les classes de plugins
            for _, obj in inspect.getmembers(module):
                if inspect.isclass(obj) and hasattr(obj, "is_plugin") and obj.is_plugin:
                    self._plugins[name] = obj

    def get_plugin(self, name: str) -> Any:
        return self._plugins.get(name)

    def list_plugins(self) -> List[str]:
        return list(self._plugins.keys())
