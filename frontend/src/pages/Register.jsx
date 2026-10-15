import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import apiClient from "../api/client";
import { useAuth } from "../context/AuthContext.jsx";
import { Notice } from "../components/ui";

// The backend returns `detail` as a string (for example "Email already registered") or, for validation errors, a list.
function getRegisterError(error) {
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (!error.response) return "Cannot reach the server. Check your connection and the API address.";
  if (Array.isArray(detail)) return "Please enter your name, a valid email address and a password.";
  return "Registration failed. Please try again.";
}

function validatePassword(password) {
  return (
    password.length >= 8 &&
    /[A-Z]/.test(password) &&
    /[^A-Za-z0-9]/.test(password)
  );
}

function Register() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const [formData, setFormData] = useState({ name: "", caregiver_email: "", password: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleChange = (event) => setFormData({ ...formData, [event.target.name]: event.target.value });

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      // POST /auth/register returns the same { access_token } as login, so the new caregiver is signed in directly.
      const response = await apiClient.post("/auth/register", formData);
      login(response.data.access_token);
      navigate("/dashboard");
    } catch (err) {
      console.error("Registration error:", err);
      setError(getRegisterError(err));
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
            <p className="muted small">Create a caregiver account · Fall Detection System</p>
          </div>
        </div>

        <form className="form" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="name">Name</label>
            <input id="name" name="name" type="text" autoComplete="name" className="input" value={formData.name} onChange={handleChange} required />
          </div>
          <div className="field">
            <label htmlFor="caregiver_email">Email</label>
            <input id="caregiver_email" name="caregiver_email" type="email" autoComplete="email" className="input" value={formData.caregiver_email} onChange={handleChange} required />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" name="password" type="password" autoComplete="new-password" className="input" value={formData.password} onChange={handleChange} required />
          </div>

          {error && <Notice tone="error">{error}</Notice>}

          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Creating account..." : "Create account"}
          </button>
        </form>

        <Link className="btn btn-secondary" to="/login">Back to login</Link>
      </div>
    </main>
  );
}

export default Register;
