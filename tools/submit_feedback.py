import argparse
import json
import time
from pathlib import Path
from typing import Optional


def _slugify(value: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in value.lower())
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-") or "documento"


def _default_base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _build_payload(args: argparse.Namespace) -> dict:
    observacoes = "\n".join(args.observacoes or []).strip()
    return {
        "documento": args.documento,
        "status": args.status.lower(),
        "nova_categoria": args.nova_categoria or "",
        "observacoes": observacoes,
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "autor": args.autor or "",
    }


def _write_payload(base_dir: Path, payload: dict, dry_run: bool) -> Path:
    feedback_dir = base_dir / "folders" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(payload["documento"])
    filename = f"feedback_{slug}_{int(time.time())}.json"
    target = feedback_dir / filename
    if dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"[dry-run] Arquivo seria salvo em: {target}")
        return target
    with open(target, "w", encoding="utf-8") as handler:
        json.dump(payload, handler, indent=2, ensure_ascii=False)
        handler.write("\n")
    return target


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Envia feedback estruturado para a pasta 'folders/feedback/'. "
            "O watcher aplica o aprendizado automaticamente."
        )
    )
    parser.add_argument("documento", help="Nome do arquivo analisado (ex.: contrato.pdf)")
    parser.add_argument(
        "--status",
        choices=["correto", "incorreto"],
        default="correto",
        help="Resultado da avaliacao humana (padrao: correto).",
    )
    parser.add_argument(
        "--nova-categoria",
        help="Categoria correta, caso o documento tenha sido classificado de forma incorreta.",
    )
    parser.add_argument(
        "--observacoes",
        "-o",
        action="append",
        help="Observacoes adicionais. Pode ser informado varias vezes para linhas diferentes.",
    )
    parser.add_argument(
        "--autor",
        help="Identificacao de quem gerou o feedback (opcional).",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=_default_base_dir(),
        help="Diretorio raiz do projeto (padrao: pasta raiz detectada automaticamente).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Exibe o JSON gerado sem gravar arquivo.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    payload = _build_payload(args)
    target = _write_payload(args.base_dir, payload, args.dry_run)
    if not args.dry_run:
        print(f"Feedback registrado em {target}")


if __name__ == "__main__":
    main()
