# Authenticode preparation — RC3

Status: BLOCKED. No signing has been executed. The owner must supply an appropriate
code-signing certificate and authorize use of its key and timestamp service.

After approval, sign both executables in build/stage before building the installer,
then sign the resulting installer. Use SHA256 file and timestamp digest algorithms
with the certificate provider's RFC3161 timestamp URL. Keep the key outside the repo.

Verify every signature using Get-AuthenticodeSignature and signtool verify /pa /all.
Re-run tools/release_manifest.py seal after executable signing, rebuild Inno, then
run tools/release_manifest.py installer --installer <signed installer>. Publish the
post-signing SHA256; the unsigned candidate hash must not be reused.

Do not invoke this procedure using an expired certificate, an unrelated publisher's
certificate, a password embedded in a command, or an unapproved remote signing service.
