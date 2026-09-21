from typing import List

from ..models import PluginInfo


PLUGIN_CATALOG: List[PluginInfo] = [
    PluginInfo(
        id="camera",
        name="Camera",
        version="1.0.0",
        platforms=["android"],
        architectures=[
            "arm64-v8a",
            "armeabi-v7a",
        ],
        capabilities=[
            "camera.capture",
            "camera.flash",
        ],
        package_url="/api/v1/plugins/camera/package",
    ),

    PluginInfo(
        id="notifications",
        name="Notifications",
        version="1.0.0",
        platforms=["android"],
        architectures=[
            "arm64-v8a",
            "armeabi-v7a",
        ],
        capabilities=[
            "notification.send",
        ],
        package_url="/api/v1/plugins/notifications/package",
    ),

    PluginInfo(
        id="browser",
        name="Browser Automation",
        version="1.0.0",
        platforms=["linux"],
        architectures=[
            "x86_64",
        ],
        capabilities=[
            "browser.open",
            "browser.navigate",
            "browser.click",
            "browser.type",
        ],
        package_url="/api/v1/plugins/browser/package",
    ),
]