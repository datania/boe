# Datos del BOE 📜

Repositorio para descargar los PDFs del BOE (Boletín Oficial del Estado) y convertirlos a Markdown.

## 🛠️ Configuración

```bash
make setup
```

## 🚀 Uso

Descarga un rango acotado de fechas:

```bash
make run ARGS="--start-date 2025-01-01 --end-date 2025-01-07"
```

Sin argumentos, el descargador procesa desde 1961 hasta hoy. Los archivos se guardan en `boe/` con esta estructura:

```text
boe/
└── YYYY/
    └── MM/
        └── DD/
            ├── boe.pdf
            └── boe.md
```

Para publicar los archivos en el dataset [`datania/boe`](https://huggingface.co/datasets/datania/boe):

```bash
HF_TOKEN="$(gopass show -o huggingface/access-token/davidgasquez)" make upload
```

El workflow semanal procesa los últimos 14 días. También admite ejecuciones manuales con `start_date` y `end_date` para backfills acotados.

## 📄 Licencia

MIT.
