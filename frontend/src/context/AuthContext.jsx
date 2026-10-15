import { createContext, useContext, useEffect, useState } from "react";
import apiClient from "../api/client";
import { clearSavedPush } from "../pushNotifications";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(
    () => localStorage.getItem("access_token")
  );

  const login = (accessToken) => {
    localStorage.setItem("access_token", accessToken);
    clearSavedPush();
    setToken(accessToken);
  };

  const logout = () => {
    localStorage.removeItem("access_token");
    clearSavedPush();
    setToken(null);
  };

  const isAuthenticated = Boolean(token);

  // If the backend rejects the saved token (for example it expired), sign out so ProtectedRoute returns to /login.
  useEffect(() => {
    const id = apiClient.interceptors.response.use(
      (response) => response,
      (error) => {
        const isLoginRequest = error.config?.url?.includes("/auth/login");
        if (error.response?.status === 401 && !isLoginRequest) {
          localStorage.removeItem("access_token");
          setToken(null);
        }
        return Promise.reject(error);
      }
    );
    return () => apiClient.interceptors.response.eject(id);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        token,
        isAuthenticated,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  return useContext(AuthContext);
}