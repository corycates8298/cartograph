from setuptools import setup, find_packages

setup(
    name="cartograph",
    version="1.0.0",
    description="Customer purchase analytics engine — map the terrain of buyer behavior",
    author="Cory Cates",
    author_email="corycates8298@gmail.com",
    url="https://github.com/corycates8298/cartograph",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "duckdb>=1.0.0",
        "pandas>=2.0.0",
        "openpyxl>=3.1.0",
        "requests>=2.28.0",
    ],
    entry_points={
        "console_scripts": [
            "cartograph=cartograph.cli:main",
            "catalog-scraper=cartograph.scraper:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Scientific/Engineering :: Information Analysis",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
    ],
)
