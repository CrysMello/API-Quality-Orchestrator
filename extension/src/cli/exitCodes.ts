export type ExitSeverity = "info" | "warning" | "error";

export interface ExitCodeInfo {
  label: string;
  severity: ExitSeverity;
  message: string;
}

// Espelha a tabela contratual e estável de src/api_quality_agent/cli/exit_codes.py
// ("números já publicados nunca são reaproveitados", conforme comentário
// no próprio arquivo Python). Mantido manualmente — não há sincronia
// automática entre os dois lados; revisar aqui a cada bump de versão do
// pyproject.toml que mexer em exit_codes.py.
const EXIT_CODES: Record<number, ExitCodeInfo> = {
  0: { label: "SUCCESS", severity: "info", message: "Concluído com sucesso." },
  1: { label: "FUNCTIONAL_FAILURE", severity: "warning", message: "Concluído, mas com falhas — veja os logs." },
  2: { label: "INVALID_INPUT_OR_CONFIGURATION", severity: "error", message: "Entrada ou configuração inválida." },
  3: {
    label: "AUTHENTICATION_FAILURE",
    severity: "error",
    message: "Falha de autenticação (verifique a POSTMAN_API_KEY configurada no ambiente).",
  },
  4: { label: "RESOURCE_NOT_FOUND", severity: "error", message: "Recurso não encontrado." },
  5: { label: "AMBIGUOUS_SELECTION", severity: "error", message: "Seleção ambígua — mais de um item corresponde." },
  6: {
    label: "INTEGRATION_FAILURE",
    severity: "error",
    message: "Falha de infraestrutura (executável ausente, timeout, ou entrada inválida para a ferramenta externa).",
  },
  7: { label: "UPDATE_NOT_APPROVED", severity: "error", message: "Atualização não aprovada." },
  8: { label: "INTERNAL_FAILURE", severity: "error", message: "Falha interna inesperada." },
  9: { label: "OPERATION_CANCELLED", severity: "info", message: "Operação cancelada." },
};

export function describeExitCode(exitCode: number | null): ExitCodeInfo {
  if (exitCode === null) {
    return {
      label: "NO_EXIT_CODE",
      severity: "error",
      message: "O processo foi encerrado sem código de saída (pode ter sido interrompido externamente).",
    };
  }
  return (
    EXIT_CODES[exitCode] ?? {
      label: `UNKNOWN(${exitCode})`,
      severity: "error",
      message: `Código de saída não mapeado: ${exitCode}.`,
    }
  );
}
