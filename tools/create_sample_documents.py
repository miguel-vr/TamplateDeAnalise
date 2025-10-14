import argparse
import shutil
import textwrap
from pathlib import Path
from typing import Dict, List


SAMPLES: List[Dict[str, str]] = [
    {
        "filename": "nota_fiscal_desconhecida.txt",
        "description": "Nota fiscal com campos faltando e itens inconsistentes.",
        "content": textwrap.dedent(
            """
            NOTA FISCAL ELETRONICA (MODELO SIMPLIFICADO)

            Razao social: Comercial Horizonte LTDA
            CNPJ: 12.345.678/0001-90
            Numero NF: 998877-XX
            Data emissao: 18/09/2025

            Itens:
              1) Servico consultivo premium .................. R$ 4.800,00
              2) Taxa extraordinaria nao detalhada ........... R$   950,00
              3) Ajuste financeiro - origem nao informada .... R$ 2.120,00

            Observacao fiscal:
              Documento enviado fora do prazo contratual.
              Codigo CNAE nao informado pelo solicitante.
              Tributacao definida posterior ao fechamento contabile.

            Informacoes adicionais ao fisco:
              Solicitado reenquadramento como servico educacional, mas a natureza
              dos itens indica consultoria financeira. Revisao obrigatoria.
            """
        ).strip(),
    },
    {
        "filename": "memorando_interno_fragmentado.txt",
        "description": "Memorando com requisicoes de compliance misturadas a temas comerciais.",
        "content": textwrap.dedent(
            """
            MEMORANDO INTERNO - USO RESTRITO
            Unidade: Operacoes Estrategicas
            Data: 05/08/2025

            Assunto:
              Revisar o processo de onboarding de fornecedores globais.

            Trechos registrados:
              - A equipe comercial aprovou um parceiro sem due diligence completa.
              - Ha contratos sem clausulas de seguranca da informacao atualizadas.
              - O time juridico sinalizou conflitos de clausulas com politicas ESG.
              - Auditoria pede plano corretivo antes do proximo comite executivo.

            Acoes urgentes:
              * Consolidar parecer do compliance e da area financeira.
              * Enviar relatorio ao comite de risco ate sexta-feira.
              * Manter registro de comunicacoes com o fornecedor contestado.
            """
        ).strip(),
    },
    {
        "filename": "contrato_parcial_sem_classificacao.txt",
        "description": "Contrato parcial com clausulas soltas e referencias cruzadas quebradas.",
        "content": textwrap.dedent(
            """
            CONTRATO DE PRESTACAO DE SERVICOS (VERSAO DE TRABALHO)

            Clausula 1 - Objeto
              A contratada atuara no projeto codinome "Atlas" com foco em integracao
              de plataformas legadas. Escopo completo pendente de aprovacao.

            Clausula 3 - Vigencia
              Prazo inicial de 14 meses, prorrogavel automaticamente caso as metas
              de compliance nao sejam atingidas ate o segundo ciclo de auditoria.

            Clausula 7 - Garantias
              Conforme anexo III (ainda nao revisado pelo time de risco cibernetico).

            Clausula 12 - Penalidades
              Indicadores de desempenho a definir com o comite regulatorio.
              Multas percentuais alinhadas ao contrato quadro 2022-REF-CORE.

            Observacoes marginais:
              Este rascunho foi extraido de e-mail interno sem revisao final.
              Necessario verificar sobreposicao com acordo trabalhista vigorex.
            """
        ).strip(),
    },
    {
        "filename": "relatorio_inovacao_confuso.txt",
        "description": "Relatorio com linguagem tecnica misturada a metricas financeiras e riscos.",
        "content": textwrap.dedent(
            """
            RELATORIO EXECUTIVO - PROGRAMA PULSAR

            Sumario rapido:
              O laboratorio de inovacao identificou tres pilotos com alto potencial,
              porem a area de controladoria aponta riscos de capitalizacao.

            Destaques tecnicos:
              - Algoritmo preditivo integrando dados sensiveis de clientes legacy.
              - Prova de conceito de assinatura digital com dependencia regulatoria.
              - Bot de atendimento que utiliza dados anonimizados parcialmente.

            Alertas:
              * Necessidade de consulta ao juridico sobre tratamento de dados.
              * Reavaliacao das garantias de compliance LGPD antes do rollout.
              * Abertura de chamado com o comite etico por uso de base historica.
            """
        ).strip(),
    },
    {
        "filename": "laudo_medico_em_contexto_corporativo.txt",
        "description": "Documento medico anexado equivocadamente ao fluxo corporativo.",
        "content": textwrap.dedent(
            """
            LAUDO MEDICO - USO CLINICO

            Paciente: ########## (dados mascarados)
            Especialidade: Medicina do Trabalho
            Data avaliacao: 22/07/2025

            Achados:
              Paciente relata ergonomia inadequada em estacao compartilhada.
              Recomendado ajuste de jornada e mobiliario, com laudo complementar.

            Observacao importante:
              Documento enviado por engano para o canal de classificacao corporativa.
              Conteudo deve ser tratado como sensivel e direcionado ao RH confidencial.
            """
        ).strip(),
    },
]


def _write_sample_file(target: Path, payload: Dict[str, str], overwrite: bool) -> None:
    if target.exists() and not overwrite:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload["content"] + "\n", encoding="utf-8")


def create_samples(base_dir: Path, overwrite: bool, drop_into_entrada: bool) -> None:
    samples_dir = base_dir / "samples"
    entrada_dir = base_dir / "folders" / "entrada"
    entrada_dir.mkdir(parents=True, exist_ok=True)

    for sample in SAMPLES:
        sample_path = samples_dir / sample["filename"]
        _write_sample_file(sample_path, sample, overwrite)
        if drop_into_entrada:
            destination = entrada_dir / sample["filename"]
            shutil.copyfile(sample_path, destination)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera documentos de exemplo desafiadores para testar o classificador."
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobrescreve arquivos existentes em samples/.",
    )
    parser.add_argument(
        "--drop-into-entrada",
        action="store_true",
        help="Copia os arquivos gerados para folders/entrada para processamento imediato.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    base_dir = Path(__file__).resolve().parent.parent
    create_samples(base_dir, overwrite=args.overwrite, drop_into_entrada=args.drop_into_entrada)
    print(f"Samples gerados em {base_dir / 'samples'}")
    if args.drop_into_entrada:
        print(f"Copias enviadas para {base_dir / 'folders' / 'entrada'}")


if __name__ == "__main__":
    main()
