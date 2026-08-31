// Roda durante o BUILD do Netlify (não em tempo de execução da função).
//
// O AWS Lambda (que roda as Netlify Functions) limita o total de variáveis de
// ambiente da função a 4KB — o wallet em base64 sozinho já passa disso. Por
// isso, em vez de a função ler o wallet de variáveis de ambiente a cada
// chamada, este script decodifica o wallet UMA VEZ, no build, e grava os
// arquivos dentro de netlify/functions/oracle-data-wallet/, que é empacotado
// junto da função (via "included_files" no netlify.toml) — sem contar para
// o limite de 4KB, porque não é mais uma variável de ambiente da função.
//
// As variáveis ORACLE_WALLET_B64_1..N só precisam existir com escopo de
// "Builds" no Netlify (não precisam do escopo "Functions/Runtime").
const fs = require("fs");
const path = require("path");
const AdmZip = require("adm-zip");

function readWalletB64() {
  let combined = "";
  for (let i = 1; i <= 20; i++) {
    const part = process.env[`ORACLE_WALLET_B64_${i}`];
    if (!part) break;
    combined += part;
  }
  return combined || process.env.ORACLE_WALLET_B64 || "";
}

const b64 = readWalletB64();
const outDir = path.join(__dirname, "..", "netlify", "functions", "oracle-data-wallet");

if (!b64) {
  console.warn(
    "[prepare-wallet] Nenhuma variável ORACLE_WALLET_B64_N encontrada no build — " +
      "a função vai usar os dados simulados de reserva até isso ser configurado."
  );
  process.exit(0);
}

fs.mkdirSync(outDir, { recursive: true });
const zipPath = path.join(outDir, "_wallet.zip");
fs.writeFileSync(zipPath, Buffer.from(b64, "base64"));
new AdmZip(zipPath).extractAllTo(outDir, true);
fs.unlinkSync(zipPath);
console.log(`[prepare-wallet] wallet extraído com sucesso em ${outDir}`);
