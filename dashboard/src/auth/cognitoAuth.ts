import type { AuthSession, UserRole } from '../types/workzone';

const AUTH_MODE = import.meta.env.VITE_AUTH_MODE || 'local_mock';
const COGNITO_REGION = import.meta.env.VITE_COGNITO_REGION || 'ap-south-1';
const COGNITO_CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID || '';

export function isCognitoMode(): boolean {
  return AUTH_MODE === 'cognito' && Boolean(COGNITO_CLIENT_ID);
}

export function decodeJwtClaims(token: string): Record<string, unknown> {
  const parts = token.split('.');
  if (parts.length < 2) {
    return {};
  }
  const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
  const json = atob(padded);
  return JSON.parse(json) as Record<string, unknown>;
}

export function resolveRoleFromClaims(claims: Record<string, unknown>): UserRole {
  const rawGroups = claims['cognito:groups'];
  const groups = Array.isArray(rawGroups)
    ? rawGroups.map((g) => String(g).toLowerCase())
    : typeof rawGroups === 'string'
      ? rawGroups.split(',').map((g) => g.trim().toLowerCase())
      : [];
  return groups.includes('authorities') || groups.includes('admin') ? 'Authorities' : 'Viewers';
}

export async function signInWithCognitoPassword(
  username: string,
  password: string,
): Promise<AuthSession> {
  if (!isCognitoMode()) {
    return { username, role: 'Authorities' };
  }

  const endpoint = `https://cognito-idp.${COGNITO_REGION}.amazonaws.com/`;
  const resp = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
    },
    body: JSON.stringify({
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: COGNITO_CLIENT_ID,
      AuthParameters: {
        USERNAME: username,
        PASSWORD: password,
      },
    }),
  });

  if (!resp.ok) {
    const errText = await resp.text();
    throw new Error(`Cognito login failed (${resp.status}): ${errText}`);
  }

  const data = (await resp.json()) as {
    AuthenticationResult?: { IdToken?: string };
  };
  const idToken = data.AuthenticationResult?.IdToken;
  if (!idToken) {
    throw new Error('Cognito did not return an IdToken.');
  }
  const claims = decodeJwtClaims(idToken);
  return {
    username: String(claims['email'] || claims['cognito:username'] || username),
    role: resolveRoleFromClaims(claims),
    idToken,
  };
}
