export default function Shutter({ children }) {
  return (
    <div className="shutter">
      <div className="drum" />
      <div className="curtain">{children}</div>
      <div className="rail">
        <i className="knob" />
        <i className="pull" />
        <i className="knob" />
      </div>
    </div>
  );
}