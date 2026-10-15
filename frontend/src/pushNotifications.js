import apiClient from "./api/client";
const VAPID_PUBLIC_KEY = import.meta.env.VITE_VAPID_PUBLIC_KEY;

// The backend has no "is this browser subscribed?" route, so the app remembers the endpoint it last saved successfully.
const SAVED_KEY = "push_saved_endpoint";

export function clearSavedPush() {
  try {
    localStorage.removeItem(SAVED_KEY);
  } catch {
    // Storage can be unavailable; the status then simply shows "not saved".
  }
}

function markSaved(endpoint) {
  try {
    localStorage.setItem(SAVED_KEY, endpoint);
  } catch {
    // See clearSavedPush.
  }
}

function isSaved(endpoint) {
  try {
    return localStorage.getItem(SAVED_KEY) === endpoint;
  } catch {
    return false;
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, "+")
    .replace(/_/g, "/");

  const rawData = window.atob(base64);
  return Uint8Array.from(rawData, (char) => char.charCodeAt(0));
}

export function isPushSupported() {
  return "Notification" in window && "serviceWorker" in navigator && "PushManager" in window;
}

// Read-only status for the UI. It never asks for permission.
export async function getPushStatus() {
  if (!isPushSupported()) return { supported: false, permission: "unsupported", subscribed: false, saved: false };
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = registration ? await registration.pushManager.getSubscription() : null;
    return {
      supported: true,
      permission: Notification.permission,
      subscribed: Boolean(subscription),
      saved: subscription ? isSaved(subscription.endpoint) : false,
    };
  } catch {
    return { supported: true, permission: Notification.permission, subscribed: false, saved: false };
  }
}

export async function subscribeToPush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    throw new Error("Push notifications are not supported.");
  }

  if (!window.isSecureContext) {
    throw new Error("Push notifications need HTTPS or localhost. Open the app from a secure address.");
  }

  if (!VAPID_PUBLIC_KEY) {
    throw new Error("VAPID public key is missing.");
  }

  // serviceWorker.ready never settles if no worker is registered, so give up after 10 seconds.
  const registration = await Promise.race([
    navigator.serviceWorker.ready,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("The service worker is not ready. Reload the page and try again.")), 10000)
    ),
  ]);

  let subscription = await registration.pushManager.getSubscription();

  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
    });
  }

  const subscriptionData = subscription.toJSON();

  await apiClient.post("/push/subscribe", subscriptionData);
  markSaved(subscription.endpoint);

  return subscriptionData;
}
