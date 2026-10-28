"use strict";

const LOGIN_HOSTS = new Set([
  "accounts.google.com", "login.microsoftonline.com", "login.live.com", "appleid.apple.com", "login.yahoo.com",
]);
// Whole path segments only (ignoring a file ending like .php), so "/login" counts but "/login-tips-for-beginners" does not.
const LOGIN_WORDS = /^(log-?in|sign-?in|sign-?on|service-?login|sso|oauth2?|authenticate|auth)$/i;
const PAYMENT_WORDS = /^(checkout|payments?|billing|pay)$/i;

const segmentsOf = (pathname) => pathname.split("/").filter(Boolean).map((part) => part.replace(/\.[a-z0-9]{2,5}$/i, ""));

function parse(href) {
  try {
    return new URL(href);
  } catch {
    return null;
  }
}

function urlLooksLikeLogin(href) {
  const url = parse(href);
  if (!url) return false;
  return LOGIN_HOSTS.has(url.hostname) || segmentsOf(url.pathname).some((part) => LOGIN_WORDS.test(part));
}

function urlLooksLikePayment(href) {
  const url = parse(href);
  return !!url && segmentsOf(url.pathname).some((part) => PAYMENT_WORDS.test(part));
}

/**
 * Decides whether a page is asking the user to sign in or to pay, from signals read in the page.
 * A visible password box or a sign-in style address is a login page. Titles alone are not trusted:
 * an article called "How to log in" is not a login page.
 */
function classifyPage({ url = "", passwordInputs = 0, cardInputs = 0 } = {}) {
  const reasons = [];
  if (passwordInputs > 0) reasons.push("a password box");
  if (urlLooksLikeLogin(url)) reasons.push("a sign-in address");
  const payment = [];
  if (cardInputs > 0) payment.push("card fields");
  if (urlLooksLikePayment(url)) payment.push("a payment address");
  return {
    login: reasons.length > 0,
    loginReason: reasons.join(" and "),
    payment: payment.length > 0,
    paymentReason: payment.join(" and "),
  };
}

module.exports = { classifyPage, urlLooksLikeLogin, urlLooksLikePayment };
