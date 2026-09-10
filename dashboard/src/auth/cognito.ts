/**
 * Cognito SRP sign-in.
 *
 * The library is imported lazily so a development build in token mode never
 * loads it. NEW_PASSWORD_REQUIRED is handled as a first-class outcome, not an
 * error: the programme creates supervisor accounts with a temporary password,
 * and the first sign-in must set a real one.
 */

export type SignInOutcome =
  | { kind: "success"; accessToken: string }
  | { kind: "new-password-required" }
  | { kind: "failure"; message: string };

export async function cognitoSignIn(
  username: string,
  password: string,
  newPassword?: string,
): Promise<SignInOutcome> {
  const poolId = import.meta.env.VITE_COGNITO_USER_POOL_ID as string | undefined;
  const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined;
  if (!poolId || !clientId) {
    return {
      kind: "failure",
      message:
        "The Cognito pool is not configured. Set VITE_COGNITO_USER_POOL_ID " +
        "and VITE_COGNITO_CLIENT_ID, or use token mode for development.",
    };
  }

  const {
    AuthenticationDetails,
    CognitoUser,
    CognitoUserPool,
  } = await import("amazon-cognito-identity-js");

  const pool = new CognitoUserPool({ UserPoolId: poolId, ClientId: clientId });
  const user = new CognitoUser({ Username: username, Pool: pool });
  const details = new AuthenticationDetails({
    Username: username,
    Password: password,
  });

  return new Promise<SignInOutcome>((resolve) => {
    user.authenticateUser(details, {
      onSuccess: (session) =>
        resolve({
          kind: "success",
          accessToken: session.getAccessToken().getJwtToken(),
        }),
      onFailure: (error: Error) =>
        resolve({ kind: "failure", message: error.message }),
      newPasswordRequired: () => {
        if (!newPassword) {
          resolve({ kind: "new-password-required" });
          return;
        }
        user.completeNewPasswordChallenge(newPassword, {}, {
          onSuccess: (session) =>
            resolve({
              kind: "success",
              accessToken: session.getAccessToken().getJwtToken(),
            }),
          onFailure: (error: Error) =>
            resolve({ kind: "failure", message: error.message }),
        });
      },
    });
  });
}
