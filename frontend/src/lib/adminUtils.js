export const DEFAULT_TA =
  "நீங்கள் சரியான இடத்தில் இருக்கிறீர்கள்.\nகீழே உள்ள பட்டனை அழுத்தி எங்கள்\nஅதிகாரப்பூர்வ கணக்குடன் நேரடியாக\nஉரையாடுங்கள். 👇";

export const DEFAULT_EN =
  "You're in the right place.\nTap the button below to chat directly\nwith our Official Account. 👇";

export function fmtError(e) {
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((d) => (typeof d?.msg === "string" ? d.msg : JSON.stringify(d))).join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return e?.message || "Something went wrong.";
}
