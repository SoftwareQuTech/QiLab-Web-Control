from setuptools import find_packages, setup

setup(
    name="qilab_web_control",
    version="0.2.0",
    description="Minimal FastAPI + Jinja2 UI to control qilab",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "app": ["templates/*.html", "static/*"],
        "app.config": ["*.json"],
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
