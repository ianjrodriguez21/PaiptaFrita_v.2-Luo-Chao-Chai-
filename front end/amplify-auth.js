"use strict";

const { domain, clientId } = window.dashboardAuth;
const returnUrl = new URL("/", window.location.origin).href;
const flowKey = "arquifusion.oauth";
const sessionKey = "arquifusion.expires";
const dashboard = document.body.hasAttribute("data-dashboard");
const status = document.querySelector("[data-auth-status]");

function base64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function showError() {
  status.textContent = "No se pudo completar el acceso. Vuelve a intentarlo.";
  status.hidden = false;
}

async function start(signup) {
  const state = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(48)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  sessionStorage.setItem(flowKey, JSON.stringify({ state, verifier, expires: Date.now() + 600000 }));
  const url = new URL(signup ? "/signup" : "/oauth2/authorize", domain);
  url.search = new URLSearchParams({
    client_id: clientId, response_type: "code", scope: "openid email",
    redirect_uri: returnUrl, state, code_challenge: base64url(new Uint8Array(digest)),
    code_challenge_method: "S256",
  });
  location.assign(url.href);
}

async function complete(parameters) {
  const stored = sessionStorage.getItem(flowKey);
  sessionStorage.removeItem(flowKey);
  history.replaceState(null, "", "/");
  const flow = JSON.parse(stored);
  if (!flow || flow.state !== parameters.get("state") || flow.expires < Date.now() || !parameters.get("code")) {
    throw new Error("Invalid authorization flow");
  }
  const response = await fetch(new URL("/oauth2/token", domain), {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "authorization_code", client_id: clientId, redirect_uri: returnUrl,
      code: parameters.get("code"), code_verifier: flow.verifier,
    }),
    credentials: "omit",
    cache: "no-store",
  });
  if (!response.ok) throw new Error("Token exchange failed");
  const tokens = await response.json();
  if (!tokens.access_token || !Number.isFinite(tokens.expires_in) || tokens.expires_in <= 0) {
    throw new Error("Invalid token response");
  }
  sessionStorage.setItem(sessionKey, String(Date.now() + Math.min(3600, tokens.expires_in) * 1000));
  location.replace("/dashboard.html");
}

function signOut(event) {
  event.preventDefault();
  sessionStorage.removeItem(sessionKey);
  sessionStorage.removeItem(flowKey);
  document.body.hidden = true;
  const url = new URL("/logout", domain);
  url.search = new URLSearchParams({ client_id: clientId, logout_uri: returnUrl });
  location.replace(url.href);
}

function checkSession() {
  if (Number(sessionStorage.getItem(sessionKey)) > Date.now()) {
    document.body.hidden = false;
  } else {
    document.body.hidden = true;
    location.replace("/");
  }
}

if (dashboard) {
  document.querySelector("[data-auth-logout]").addEventListener("submit", signOut);
  addEventListener("pageshow", checkSession);
  checkSession();
} else {
  for (const link of document.querySelectorAll("[data-auth]")) {
    link.addEventListener("click", event => {
      event.preventDefault();
      start(link.dataset.auth === "signup").catch(showError);
    });
  }
  const parameters = new URLSearchParams(location.search);
  if (parameters.has("code") || parameters.has("error")) {
    complete(parameters).catch(showError);
  } else if (Number(sessionStorage.getItem(sessionKey)) > Date.now()) {
    location.replace("/dashboard.html");
  }
}
