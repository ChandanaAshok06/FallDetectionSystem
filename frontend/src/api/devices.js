import apiClient from "./client";
import { asList } from "../utils/format";

// The backend exposes POST /devices/register and GET /devices/{id}. Some backends also add GET /devices (a list).
// If the list route is missing (404/405) the device list is rebuilt from devices registered in this browser plus
// the device IDs seen in predictions and alerts, each confirmed with GET /devices/{id} (which checks ownership).
const KNOWN_KEY = "known_device_ids";

function readKnown() {
  try {
    const value = JSON.parse(localStorage.getItem(KNOWN_KEY) || "[]");
    return Array.isArray(value) ? value.filter((id) => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function writeKnown(ids) {
  try {
    localStorage.setItem(KNOWN_KEY, JSON.stringify(ids));
  } catch {
    // Storage can be unavailable (private mode); the list then relies on predictions and alerts only.
  }
}

export function rememberDevice(id) {
  if (!id) return;
  const ids = readKnown();
  if (!ids.includes(id)) writeKnown([...ids, id]);
}

export function forgetDevice(id) {
  writeKnown(readKnown().filter((item) => item !== id));
}

// Resolves to { data: [...devices], fallback: boolean } so callers can keep using `response.data`.
export async function listDevices() {
  try {
    const response = await apiClient.get("/devices");
    return { data: asList(response.data), fallback: false };
  } catch (error) {
    const status = error?.response?.status;
    if (status !== 404 && status !== 405) throw error;
  }

  const ids = new Set(readKnown());
  const [predictions, alerts] = await Promise.allSettled([apiClient.get("/predictions"), apiClient.get("/alerts")]);
  [predictions, alerts].forEach((result) => {
    if (result.status === "fulfilled") {
      asList(result.value.data).forEach((item) => item?.device_id && ids.add(item.device_id));
    }
  });

  const found = await Promise.allSettled(
  [...ids].map((id) => apiClient.get(`/devices/${id}`))
);

const data = found
  .filter((result) => result.status === "fulfilled")
  .map((result) => result.value.data);

const failedIds = found
  .map((result, index) => ({ result, id: [...ids][index] }))
  .filter(({ result }) => result.status === "rejected")
  .map(({ id, result }) => ({
    id,
    status: result.reason?.response?.status ?? "network error",
  }));

console.info("[SafeStep] Device discovery:", {
  discoveredIds: [...ids].length,
  accessibleDevices: data.length,
  inaccessibleDevices: failedIds,
});

return { data, fallback: true };
}
