// Rode isso UMA VEZ, na sua máquina, pra gerar uma versão criptografada do wallet
// que pode ficar tranquila no repositório público (sem a senha escolhida aqui,
// o arquivo é inútil pra qualquer um que o veja — inclusive pra mim).
//
// Uso:
//   node scripts/encrypt-wallet.js "C:\Users\Andre_Aragon\Downloads\Wallet_bronkalertdb.zip"
//
// Ele pede uma senha NOVA (escolha uma, não precisa ser a do banco nem a do
// wallet original — essa é só pra proteger o arquivo no repositório). Guarde
// essa senha: ela vira a variável de ambiente WALLET_DECRYPT_PASSPHRASE no
// Netlify — nunca a digite pra mim.
//
// Gera: secrets/wallet.enc — esse arquivo pode ser commitado normalmente.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");

function askHidden(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const originalWrite = rl._writeToOutput.bind(rl);
    rl._writeToOutput = function (str) {
      if (str.trim() === question.trim() || str.includes("\n")) originalWrite(str);
    };
    rl.question(question, (answer) => {
      rl.close();
      process.stdout.write("\n");
      resolve(answer);
    });
  });
}

async function main() {
  const walletPath = process.argv[2];
  if (!walletPath) {
    console.error('Uso: node scripts/encrypt-wallet.js "<caminho do wallet .zip>"');
    process.exit(1);
  }
  if (!fs.existsSync(walletPath)) {
    console.error("Arquivo não encontrado:", walletPath);
    process.exit(1);
  }

  const passphrase = await askHidden("Escolha uma senha nova para proteger o wallet no repositório (guarde-a!): ");
  const confirm = await askHidden("Confirme a senha: ");
  if (!passphrase || passphrase !== confirm) {
    console.error("As senhas não coincidem ou estão vazias. Nada foi gerado.");
    process.exit(1);
  }

  const plain = fs.readFileSync(walletPath);
  const salt = crypto.randomBytes(16);
  const iv = crypto.randomBytes(12);
  const key = crypto.scryptSync(passphrase, salt, 32);
  const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
  const ciphertext = Buffer.concat([cipher.update(plain), cipher.final()]);
  const authTag = cipher.getAuthTag();

  const out = Buffer.concat([salt, iv, authTag, ciphertext]);
  const outDir = path.join(__dirname, "..", "secrets");
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, "wallet.enc");
  fs.writeFileSync(outPath, out);

  console.log("\nGerado:", outPath);
  console.log("Esse arquivo pode ir pro repositório (git add/commit) sem problema.");
  console.log("Guarde a senha que você digitou — ela vira WALLET_DECRYPT_PASSPHRASE no Netlify.");
}

main();
