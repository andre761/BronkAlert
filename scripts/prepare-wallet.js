// Roda durante o BUILD do Netlify (não em tempo de execução da função).
//
// O AWS Lambda (que roda as Netlify Functions) limita o total de variáveis de
// ambiente da função a 4KB — o wallet nunca caberia ali. Em vez disso, o
// wallet fica criptografado no próprio repositório (secrets/wallet.enc,
// gerado por scripts/encrypt-wallet.js) e só a SENHA pequena
// (WALLET_DECRYPT_PASSPHRASE) vive numa variável de ambiente. Este script
// descriptografa o arquivo uma vez, no build, e grava os arquivos do wallet
// em netlify/functions/oracle-data-wallet/, que é empacotado junto da função
// (netlify.toml -> included_files).
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const AdmZip = require("adm-zip");

const encPath = path.join(__dirname, "..", "secrets", "wallet.enc");
const outDir = path.join(__dirname, "..", "netlify", "functions", "oracle-data-wallet");
const passphrase = process.env.WALLET_DECRYPT_PASSPHRASE;

if (!fs.existsSync(encPath)) {
  console.warn("[prepare-wallet] secrets/wallet.enc não encontrado — rode scripts/encrypt-wallet.js primeiro. A função vai usar dados simulados até isso existir.");
  process.exit(0);
}
if (!passphrase) {
  console.warn("[prepare-wallet] WALLET_DECRYPT_PASSPHRASE não configurado no Netlify — usando dados simulados.");
  process.exit(0);
}

const buf = fs.readFileSync(encPath);
const salt = buf.subarray(0, 16);
const iv = buf.subarray(16, 28);
const authTag = buf.subarray(28, 44);
const ciphertext = buf.subarray(44);

const key = crypto.scryptSync(passphrase, salt, 32);
const decipher = crypto.createDecipheriv("aes-256-gcm", key, iv);
decipher.setAuthTag(authTag);

let zipBuf;
try {
  zipBuf = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
} catch (e) {
  console.error("[prepare-wallet] Falha ao descriptografar (senha errada?):", e.message);
  process.exit(0); // não quebra o build; a função cai no fallback simulado
}

fs.mkdirSync(outDir, { recursive: true });
const tmpZip = path.join(outDir, "_wallet.zip");
fs.writeFileSync(tmpZip, zipBuf);
new AdmZip(tmpZip).extractAllTo(outDir, true);
fs.unlinkSync(tmpZip);
console.log("[prepare-wallet] wallet descriptografado e extraído em", outDir);
