# BOE Data Export

Repo to download all BOE (Boletín Oficial del Estado) PDFs.

## Setup

```bash
make setup
```

## Usage

```bash
make run
```

PDFs will be downloaded to the `boe/` directory with the structure:

```
boe/
└── YYYY/
    └── MM/
        └── DD/
            └── boe.pdf
```
