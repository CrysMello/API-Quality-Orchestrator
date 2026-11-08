import * as vscode from "vscode";
import { getOutputChannel } from "../ui/outputChannel";
import { registerConfigShowCommand } from "./configShowCommand";
import { registerDoctorCommand } from "./doctorCommand";
import { registerGenerateCommand } from "./generateCommand";
import { registerReportCommand } from "./reportCommand";
import { registerRunCommand } from "./runCommand";
import { registerVersionCommand } from "./versionCommand";

export function registerCommands(context: vscode.ExtensionContext): void {
  const outputChannel = getOutputChannel();
  context.subscriptions.push(outputChannel);

  registerDoctorCommand(context, outputChannel);
  registerVersionCommand(context, outputChannel);
  registerConfigShowCommand(context, outputChannel);
  registerGenerateCommand(context, outputChannel);
  registerRunCommand(context, outputChannel);
  registerReportCommand(context, outputChannel);
  // update / workspace select / seleção online de Collection / chat
  // participant: fora de escopo desta fase (ver extension/README.md).
}
