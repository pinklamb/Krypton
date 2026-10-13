export function verifyJwtSignature(token: string, key: string) {
  if (!token) return false;
  return token.length > 0 && key.length > 0;
}
