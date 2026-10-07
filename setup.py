# Copyright (c) 2026, Quantum Internet Alliance, Delft University of Technology, Milen Kolev, Stephanie Wehner
# SPDX-License-Identifier: BSD-3-Clause-Clear

from setuptools import find_packages, setup

setup(
    name="qilab_web_control",
    version="0.4.2",
    description="Minimal FastAPI + Jinja2 UI to control qilab",
    author="Milen Kolev, Stephanie Wehner",
    license="BSD-3-Clause-Clear",
    license_files=["LICENSE"],
    packages=find_packages(),
    include_package_data=True,
    package_data={
        # deploy/: self_update.sh + the reference systemd unit. They ship inside
        # the wheel so the deployed assets always match the installed version;
        # see README.md "Deployment layout".
        "app": ["templates/*.html", "static/*", "deploy/*"],
        "app.config": ["*.json", "VENDORED_SCHEMA_VERSION"],
    },
    install_requires=[
        "fastapi==0.110.0",
        "jinja2==3.1.3",
        "uvicorn==0.27.1",
        "python-multipart==0.0.9",
        "PyYAML==6.0.2",
        "jsonschema>=4.18",
    ],
    extras_require={
        # httpx<0.28: starlette 0.36.3's TestClient uses the removed `app=` shortcut.
        "tests": ["pytest", "httpx<0.28"],
    },
    python_requires=">=3.10",
)
