import { useState } from "react";
import { useNavigate } from "react-router-dom";
import apiClient from "../api/client";
import { useAuth } from "../context/AuthContext.jsx";
import { Notice } from "../components/ui";

// The backend can return `detail` as a string or, for validation errors, as a list of objects.
// Only strings are shown directly; anything else would crash React when rendered.
function getLoginError(error) {
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (!error.response) return "Cannot reach the server. Check your connection and the API address.";
  if (Array.isArray(detail)) return "Please enter a valid email address and password.";
  return "Login failed. Please check your email and password.";
}

function Login() {
  const navigate = useNavigate();
  const { login } = useAuth();

  const [formData, setFormData] = useState({
  caregiver_email: "",
  password: "",
});

  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleChange = (event) => {
    setFormData({
      ...formData,
      [event.target.name]: event.target.value,
    });
  };

  const handleSubmit = async (event) => {
    event.preventDefault();

    setError("");
    setLoading(true);

    try {
      const response = await apiClient.post("/auth/login", formData);

      const { access_token } = response.data;

      login(access_token);

      navigate("/dashboard");
    } catch (error) {
      console.error("Login error:", error);

      setError(getLoginError(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="brand-icon">+</div>
          <div>
            <h1>SafeStep</h1>
            <p className="muted small">Caregiver login · Fall Detection System</p>
          </div>
        </div>

        <form className="form" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="caregiver_email">Email</label>
            <input
              id="caregiver_email"
              name="caregiver_email"
              type="email"
              autoComplete="email"
              className="input"
              value={formData.caregiver_email}
              onChange={handleChange}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              className="input"
              value={formData.password}
              onChange={handleChange}
              required
            />
          </div>

          {error && <Notice tone="error">{error}</Notice>}

          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Logging in..." : "Login"}
          </button>
        </form>

        <button type="button" className="btn btn-secondary" onClick={() => navigate("/register")}>
          Create an account
        </button>
      </div>
    </main>
  );
}

export default Login;
